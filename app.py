from flask import Flask, render_template, request, jsonify, send_from_directory, make_response, session, redirect, url_for
import os
import re
import io
import sys
import json
import hashlib
import secrets
import datetime
import sqlite3
import requests
import time
import threading
from dotenv import load_dotenv
from requests_oauthlib import OAuth1

load_dotenv()

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

# ── Single-user authentication ───────────────────────────────────────────────
# This is a personal database. When APP_PASSWORD is set, a signed session cookie
# gates the whole app so an exposed URL (Render's public host, Tailscale, etc.)
# can't be tampered with. Leave APP_PASSWORD unset to run wide open (local dev).
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

# Cookies are signed with this key. Must stay STABLE or every session is
# invalidated on restart/deploy — set APP_SECRET_KEY in the environment
# (required on Render: its filesystem is ephemeral). Locally we persist a random
# key to a git-ignored file so logins survive server restarts.
_secret = os.environ.get("APP_SECRET_KEY") or os.environ.get("SECRET_KEY")
if not _secret:
    _keyfile = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".flask_secret")
    try:
        with open(_keyfile) as f:
            _secret = f.read().strip()
    except OSError:
        _secret = secrets.token_hex(32)
        try:
            with open(_keyfile, "w") as f:
                f.write(_secret)
        except OSError:
            pass  # read-only FS (Render) — env var should supply the key there
app.secret_key = _secret
app.config.update(
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(days=90),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("RENDER") is not None,  # HTTPS-only in prod
)

# Endpoints reachable without a session: the login page itself, the service
# worker, the manifest, all /static assets (so the PWA shell can load), and the
# passkey *authentication* + status endpoints (used before sign-in).
_AUTH_EXEMPT = {
    "login", "service_worker", "web_manifest", "static",
    "passkey_registered", "passkey_auth_begin", "passkey_auth_complete",
}


@app.before_request
def _require_login():
    if not APP_PASSWORD:
        return  # auth disabled
    if request.endpoint in _AUTH_EXEMPT:
        return
    if session.get("auth"):
        return
    if request.path.startswith("/api/"):
        return jsonify({"error": "Authentication required"}), 401
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not APP_PASSWORD:
        return redirect(url_for("index"))
    error = None
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("password", ""), APP_PASSWORD):
            session.permanent = True
            session["auth"] = True
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") else url_for("index"))
        error = "Incorrect password."
    resp = make_response(render_template("login.html", error=error))
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Passkeys (WebAuthn / Face ID) ────────────────────────────────────────────
# Optional Face-ID fast-path layered on top of the password. You sign in once
# with the password, register a passkey, then unlock biometrically thereafter.
# The password always remains as the bootstrap/recovery path.
#
# Render's filesystem is ephemeral, so a registered credential written to a file
# is wiped on the next deploy. To survive that, registration returns an env_blob
# you paste into the PASSKEY_CREDENTIALS env var (it stores PUBLIC keys only —
# not a secret). Locally, credentials persist to git-ignored .passkeys.json.
try:
    import webauthn as _wa
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria, ResidentKeyRequirement,
        UserVerificationRequirement, PublicKeyCredentialDescriptor,
    )
    from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
    _PASSKEYS_OK = True
except Exception as _e:      # library missing → feature degrades off
    _PASSKEYS_OK = False
    print(f"⚠ Passkeys disabled (webauthn import failed): {_e}")

_WA_USER_ID = b"brick-scanner-owner"      # single user → stable handle
_PASSKEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".passkeys.json")


def _rp_id_origin():
    """Relying-Party id (bare domain) + expected origin for the current request.
    Honour X-Forwarded-Proto so the origin is https behind Render's proxy."""
    host = request.host                       # e.g. brick-scanner.onrender.com[:port]
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    return host.split(":")[0], f"{scheme}://{host}"


def _load_passkeys():
    """Registered credentials, merged from the env var (Render) and the local
    file, de-duped by credential id."""
    creds = {}
    raw = os.environ.get("PASSKEY_CREDENTIALS", "")
    if raw:
        try:
            for c in json.loads(raw):
                creds[c["id"]] = c
        except Exception as e:
            print(f"⚠ PASSKEY_CREDENTIALS parse error: {e}")
    try:
        with open(_PASSKEY_FILE) as f:
            for c in json.load(f):
                creds[c["id"]] = c
    except (OSError, ValueError):
        pass
    return list(creds.values())


def _save_passkeys(creds):
    try:
        with open(_PASSKEY_FILE, "w") as f:
            json.dump(creds, f)
    except OSError:
        pass      # read-only/ephemeral FS (Render) — env_blob carries it instead


@app.route("/api/passkey/registered")
def passkey_registered():
    return jsonify({
        "enabled": bool(APP_PASSWORD) and _PASSKEYS_OK,
        "has_passkey": len(_load_passkeys()) > 0,
        "authed": bool(session.get("auth")),
    })


@app.route("/api/passkey/register/begin", methods=["POST"])
def passkey_register_begin():
    if not _PASSKEYS_OK:
        return jsonify({"error": "Passkeys unavailable"}), 500
    rp_id, _ = _rp_id_origin()
    opts = _wa.generate_registration_options(
        rp_id=rp_id,
        rp_name="Brick Scanner",
        user_id=_WA_USER_ID,
        user_name="owner",
        user_display_name="Brick Scanner Owner",
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["id"]))
            for c in _load_passkeys()
        ],
    )
    session["wa_reg_chal"] = bytes_to_base64url(opts.challenge)
    return app.response_class(_wa.options_to_json(opts), mimetype="application/json")


@app.route("/api/passkey/register/complete", methods=["POST"])
def passkey_register_complete():
    if not _PASSKEYS_OK:
        return jsonify({"error": "Passkeys unavailable"}), 500
    chal = session.pop("wa_reg_chal", None)
    if not chal:
        return jsonify({"error": "No registration in progress"}), 400
    rp_id, origin = _rp_id_origin()
    try:
        ver = _wa.verify_registration_response(
            credential=request.get_data(as_text=True),
            expected_challenge=base64url_to_bytes(chal),
            expected_rp_id=rp_id,
            expected_origin=origin,
        )
    except Exception as e:
        return jsonify({"error": f"Verification failed: {e}"}), 400
    body = request.get_json(silent=True) or {}
    transports = (body.get("response") or {}).get("transports") or []
    new_id = bytes_to_base64url(ver.credential_id)
    creds = [c for c in _load_passkeys() if c["id"] != new_id]
    creds.append({
        "id": new_id,
        "public_key": bytes_to_base64url(ver.credential_public_key),
        "sign_count": ver.sign_count,
        "transports": transports,
        "added": datetime.date.today().isoformat(),
    })
    _save_passkeys(creds)
    return jsonify({"ok": True, "count": len(creds),
                    "env_blob": json.dumps(creds, separators=(",", ":"))})


@app.route("/api/passkey/auth/begin", methods=["POST"])
def passkey_auth_begin():
    if not _PASSKEYS_OK:
        return jsonify({"error": "Passkeys unavailable"}), 500
    creds = _load_passkeys()
    if not creds:
        return jsonify({"error": "No passkey registered"}), 404
    rp_id, _ = _rp_id_origin()
    opts = _wa.generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["id"]))
            for c in creds
        ],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    session["wa_auth_chal"] = bytes_to_base64url(opts.challenge)
    return app.response_class(_wa.options_to_json(opts), mimetype="application/json")


@app.route("/api/passkey/auth/complete", methods=["POST"])
def passkey_auth_complete():
    if not _PASSKEYS_OK:
        return jsonify({"error": "Passkeys unavailable"}), 500
    chal = session.pop("wa_auth_chal", None)
    if not chal:
        return jsonify({"error": "No authentication in progress"}), 400
    body = request.get_json(silent=True) or {}
    cred_id = body.get("id") or body.get("rawId")
    creds = _load_passkeys()
    match = next((c for c in creds if c["id"] == cred_id), None)
    if not match:
        return jsonify({"error": "Unknown passkey"}), 400
    rp_id, origin = _rp_id_origin()
    try:
        ver = _wa.verify_authentication_response(
            credential=request.get_data(as_text=True),
            expected_challenge=base64url_to_bytes(chal),
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(match["public_key"]),
            credential_current_sign_count=match.get("sign_count", 0),
        )
    except Exception as e:
        return jsonify({"error": f"Verification failed: {e}"}), 400
    match["sign_count"] = ver.new_sign_count       # local persistence only
    _save_passkeys(creds)
    session.permanent = True
    session["auth"] = True
    return jsonify({"ok": True})


BL_CONSUMER_KEY    = os.environ.get("BL_CONSUMER_KEY", "")
BL_CONSUMER_SECRET = os.environ.get("BL_CONSUMER_SECRET", "")
BL_TOKEN           = os.environ.get("BL_TOKEN", "")
BL_TOKEN_SECRET    = os.environ.get("BL_TOKEN_SECRET", "")

API_KEY = os.environ.get("REBRICKABLE_API_KEY", "")
USER_TOKEN = os.environ.get("REBRICKABLE_USER_TOKEN", "")
RB_BASE = "https://rebrickable.com/api/v3"
BL_BASE = "https://api.bricklink.com/api/store/v1"


class _PooledSession(requests.Session):
    """Shared outbound HTTP session: connection pooling (keep-alive instead of
    a fresh TLS handshake per call) and a default 10s timeout so no call can
    hang a worker thread. Explicit timeout= kwargs still win."""
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", 10)
        return super().request(method, url, **kwargs)


http = _PooledSession()
PART_COLOR_IMAGE_CACHE = {}
COLORS_CACHE = {"data": None, "timestamp": None}  # Cache colors to avoid repeated API calls
COLORS_CACHE_DURATION = 3600  # 1 hour in seconds

# ── Offline catalog (local SQLite built from the Rebrickable CSV dump) ─────────
# build_brick_db.py loads "Brick Parts/*.csv" into this file. It powers offline
# search (parts/minifigs/sets) so lookups don't consume the 60 req/min API quota.
# Absent on production → offline search degrades gracefully (returns a notice).
_HERE = os.path.dirname(os.path.abspath(__file__))
LOCAL_DB_PATH = os.path.join(_HERE, "brick_parts.db")
CATALOG_MANIFEST_PATH = os.path.join(_HERE, ".catalog_manifest.json")
CATALOG_CHANGES_PATH = os.path.join(_HERE, ".catalog_changes.json")
CATALOG_LAST_CHECKED_PATH = os.path.join(_HERE, ".catalog_last_checked")

# Local JSON stores (keyed by set_num / fig_num). LOCAL-ONLY: Render's
# filesystem is ephemeral, so these stay empty there (the public site shows
# blanks).
#   SET_META_PATH         — purchase metadata (condition + price paid) for owned
#                           sets; quantity itself lives in Rebrickable's set
#                           collection, which can't hold this extra info.
#   MINIFIG_COLLECTION_PATH — the entire "My Minifigs" collection (quantity +
#                           condition + price + display name/image). Rebrickable's
#                           minifig endpoint is read-only (derived from owned
#                           sets), so the collection can't live there.
SET_META_PATH = os.path.join(_HERE, ".set_meta.json")
MINIFIG_COLLECTION_PATH = os.path.join(_HERE, ".minifig_collection.json")
#   PART_BINS_PATH        — physical sorting-bin location per part ({part_num:
#                           "A3"}). Keyed by part number only: a mould lives in
#                           one bin regardless of colour or which list holds it.
PART_BINS_PATH = os.path.join(_HERE, ".part_bins.json")
#   SUBTRACT_RECORDS_PATH — record of each "Subtract a Set" run: which of the
#                           set's pieces were pulled into the list (subtracted)
#                           vs. which weren't needed and are now spare (remaining).
#                           Keyed "{set_num}__{list_id}" → {set_name, set_img,
#                           list_name, subtracted:[…], remaining:[…], …, building}.
#                           Shown as collapsible cards on the Part Lists tab.
SUBTRACT_RECORDS_PATH = os.path.join(_HERE, ".subtract_records.json")
_meta_lock = threading.Lock()


def _load_meta(path):
    """Read a purchase-metadata map ({num: {...}}) from path; {} if absent."""
    try:
        with open(path) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save_meta(path, meta):
    """Atomically write a purchase-metadata map to path."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f)
    os.replace(tmp, path)


def _clean_meta(entry):
    """Normalize a stored/raw meta entry to {condition, price_paid} or None.
    condition ∈ {"used","new",None}; price_paid is a float or None."""
    if not isinstance(entry, dict):
        return None
    cond = entry.get("condition")
    cond = cond if cond in ("used", "new") else None
    price = entry.get("price_paid")
    try:
        price = float(price) if price not in (None, "") else None
    except (TypeError, ValueError):
        price = None
    if cond is None and price is None:
        return None
    return {"condition": cond, "price_paid": price}

# Manual catalog-refresh state (the "refresh now" button on the scan screen).
# LOCAL-ONLY FEATURE: refresh + change tracking are disabled on Render. Render's
# filesystem is ephemeral (DB rebuilt from scratch each deploy), so there's no
# prior catalog to diff and no rebuild trigger. When IS_RENDER, can_refresh is
# false (footer hidden) and /api/catalog/refresh returns 403.
IS_RENDER = os.environ.get("RENDER") is not None
_catalog_lock = threading.Lock()
_catalog_state = {"running": False, "last_result": None}


def local_db():
    """Return a read-only-ish connection to the local catalog, or None if absent."""
    if not os.path.exists(LOCAL_DB_PATH):
        return None
    conn = sqlite3.connect(LOCAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Local-catalog lookups used during a scan (each falls back to the live API
#    in the caller when it returns None, so production without a DB is unchanged).

def _local_resolve_part(bl_id):
    """Map a BrickLink part id → Rebrickable part using the local catalog.

    Heuristic: Rebrickable aligns its part_num with BrickLink's for the vast
    majority of standard parts, so a BrickLink id that exists verbatim in the
    parts table is the same physical part. Printed/variant parts whose numbers
    differ simply miss here → caller falls back to the authoritative API.
    Returns {part_num, name, img_url} or None.
    """
    if not bl_id:
        return None
    conn = local_db()
    if conn is None:
        return None
    try:
        # 1. Exact identity match — covers the vast majority of standard parts.
        row = conn.execute(
            "SELECT part_num, name, img_url FROM parts WHERE part_num = ?",
            (bl_id,),
        ).fetchone()
        if row:
            return dict(row)

        # 2. Authoritative BrickLink→Rebrickable alias, harvested from Rebrickable's
        #    external_ids (bl_aliases table). A BrickLink id can map to several
        #    Rebrickable molds (e.g. 3068 → 3068a/3068b) — pick the one in the most
        #    set inventories. Wrapped in try/except so a DB built before this table
        #    existed degrades to the heuristic below.
        try:
            row = conn.execute(
                """
                SELECT p.part_num, p.name, p.img_url,
                       (SELECT COUNT(*) FROM inventory_parts ip
                        WHERE ip.part_num = p.part_num) AS freq
                FROM bl_aliases a
                JOIN parts p ON p.part_num = a.part_num
                WHERE a.bl_id = ?
                ORDER BY freq DESC, p.part_num
                LIMIT 1
                """,
                (bl_id,),
            ).fetchone()
            if row:
                return {"part_num": row["part_num"], "name": row["name"],
                        "img_url": row["img_url"]}
        except sqlite3.OperationalError:
            pass  # bl_aliases table absent (older DB) — fall through to heuristic

        # 3. Mold-variant fallback. BrickLink often uses a bare number (e.g. 3068)
        #    where Rebrickable splits molds with a single-letter suffix
        #    (3068a "without groove" / 3068b "with groove"). Match part_num =
        #    bl_id + exactly one lowercase letter (GLOB has no trailing *, so
        #    printed variants like 3068bpr0001 are excluded) and pick the variant
        #    that appears in the most set inventories — i.e. the common modern
        #    part (3068b, 7961 sets, over 3068a, 144). This keeps the single most
        #    common LEGO parts resolving locally instead of depending on a live
        #    API call that can time out or hit the rate limit mid-scan.
        if bl_id.isalnum():
            row = conn.execute(
                """
                SELECT p.part_num, p.name, p.img_url,
                       (SELECT COUNT(*) FROM inventory_parts ip
                        WHERE ip.part_num = p.part_num) AS freq
                FROM parts p
                WHERE p.part_num GLOB ?
                ORDER BY freq DESC, p.part_num
                LIMIT 1
                """,
                (bl_id + "[a-z]",),
            ).fetchone()
            if row:
                return {"part_num": row["part_num"], "name": row["name"],
                        "img_url": row["img_url"]}
        return None
    finally:
        conn.close()


def _local_all_colors():
    """Full color list from the local catalog in Rebrickable-API shape, or None.

    The catalog's colors table is complete (~275 colors) and instant, vs the
    live Rebrickable fetch which is rate-limited and degrades to a tiny 45-color
    FALLBACK list that omits Medium Azure and most specialty colors — which made
    color matching pick a wrong nearby color. Returns [{id,name,rgb,is_trans}].
    """
    conn = local_db()
    if conn is None:
        return None
    try:
        rows = conn.execute("SELECT id, name, rgb, is_trans FROM colors").fetchall()
        return [
            {"id": r["id"], "name": r["name"], "rgb": r["rgb"] or "",
             "is_trans": str(r["is_trans"]).strip().lower() in ("true", "1", "t")}
            for r in rows
        ]
    finally:
        conn.close()


_COLOR_ID_BY_NAME = None   # lazy {lowercase name: id} map of the colors table


def _local_color_id_by_name(name):
    """Resolve a color NAME → Rebrickable color id via the local catalog.

    Brickognize returns BrickLink-namespaced color ids (e.g. color-156 =
    Medium Azure), not Rebrickable ids, but the color *names* line up. Returns
    the int id or None. The colors table is tiny and static, so it's loaded
    once into memory — identify calls this per candidate color, and opening a
    SQLite connection each time was the scan path's hottest redundant work.
    """
    global _COLOR_ID_BY_NAME
    if not name:
        return None
    if _COLOR_ID_BY_NAME is None:
        colors = _local_all_colors()
        if colors is None:
            return None   # no local DB — leave uncached so it works if one appears
        _COLOR_ID_BY_NAME = {c["name"].lower(): c["id"] for c in colors}
    return _COLOR_ID_BY_NAME.get(name.lower())


# Gate for fuzzy BrickLink→Rebrickable minifig name matches. The two catalogs
# name figs differently and a brand-new fig may not be in Rebrickable at all, so
# an unbounded "most shared words" match can bind the wrong fig (e.g. 'Young Link'
# for 'Link - Dark Azure Champion's Tunic', sharing only 'link'). Require a solid
# fraction of the BrickLink name's significant words to match before trusting it.
_MINIFIG_MATCH_MIN = 0.5


def _minifig_name_words(name):
    """Significant lowercase word tokens of a minifig name (drops 1-char tokens
    like a possessive 's so 'Champion's' contributes 'champion')."""
    return {w for w in re.findall(r'[a-z0-9]+', (name or "").lower()) if len(w) > 1}


def _minifig_match_score(bl_name, cand_name):
    """Fraction (0..1) of `bl_name`'s significant words also in `cand_name`."""
    bw = _minifig_name_words(bl_name)
    return (len(bw & _minifig_name_words(cand_name)) / len(bw)) if bw else 0.0


def _local_resolve_minifig(name):
    """Find the best fig_num in the local catalog by word overlap with `name`,
    mirroring the live-API search heuristic. Returns {fig_num, name, img_url} or
    None — including when the best match is too weak to trust (see
    _MINIFIG_MATCH_MIN), so the caller can fall back to a BrickLink-only entry.
    """
    if not name:
        return None
    conn = local_db()
    if conn is None:
        return None
    try:
        search_name = re.split(r' - | \(', name)[0].strip()
        if not search_name:
            return None
        rows = conn.execute(
            "SELECT fig_num, name, img_url FROM minifigs WHERE name LIKE ? LIMIT 50",
            (f"%{search_name}%",),
        ).fetchall()
        if not rows:
            return None
        full_words = set(re.findall(r'\w+', name.lower()))
        best = max(rows, key=lambda r: len(full_words & set(re.findall(r'\w+', r["name"].lower()))))
        if _minifig_match_score(name, best["name"]) < _MINIFIG_MATCH_MIN:
            return None
        return dict(best)
    finally:
        conn.close()


def _bricklink_minifig_lookup(bl_id):
    """Look up a BrickLink minifig id (e.g. sw0131) → {name, img_url} via the
    BrickLink API. Rebrickable exposes no BrickLink minifig ids, so this is the
    only bridge to a Rebrickable fig — and, when there's no Rebrickable match at
    all (a brand-new figure BrickLink's crowd-sourced catalog has but Rebrickable
    doesn't yet), the source of truth for adding the figure straight from
    BrickLink data. Returns {"name", "img_url"} or None on any failure;
    `img_url` may be None (BrickLink's protocol-relative URL is normalized to https).
    """
    if not (BL_CONSUMER_KEY and BL_TOKEN):
        return None
    try:
        auth = OAuth1(BL_CONSUMER_KEY, BL_CONSUMER_SECRET, BL_TOKEN, BL_TOKEN_SECRET)
        resp = http.get(f"{BL_BASE}/items/MINIFIG/{bl_id}", auth=auth, timeout=8)
        if resp.status_code == 200:
            d = ((resp.json() or {}).get("data") or {})
            name = d.get("name")
            if not name:
                return None
            img = d.get("image_url")
            if img and img.startswith("//"):
                img = "https:" + img
            return {"name": name, "img_url": img or None}
    except Exception:
        pass
    return None


BL_MINIFIG_VARIANTS_PATH = os.path.join(_HERE, ".bl_minifig_variants.json")
MINIFIG_INDEX_PATH = os.path.join(_HERE, "minifig_variants.json")
_VARIANT_TTL_DAYS = 30


def _minifig_base_id(bl_id):
    """Numeric base of a BrickLink minifig id — strips a trailing variant suffix.
    'sw0574a' → 'sw0574'; 'sw0574' → 'sw0574'. None if it isn't a minifig id."""
    m = re.match(r'^([a-z]+\d+)[a-z]*$', (bl_id or "").strip().lower())
    return m.group(1) if m else None


def _bl_minifig_img_url(bl_id):
    """BrickLink's canonical minifig image URL — constructible from the id alone,
    so the offline variant index needs no API call to surface thumbnails."""
    return f"https://img.bricklink.com/ItemImage/MN/0/{bl_id}.png"


# Offline variant index — base id -> [{id, name}, …], grouped once at first use
# from the committed minifig_variants.json (built by build_minifig_index.py from
# BrickLink's catalog download). Complete, instant, and creds-free, so unlike the
# live probe it works on Render. None until loaded; {} if the file is absent.
_minifig_index = None


def _load_minifig_index():
    global _minifig_index
    if _minifig_index is not None:
        return _minifig_index
    idx = {}
    try:
        with open(MINIFIG_INDEX_PATH) as f:
            data = json.load(f)
        for mid, name in (data.get("minifigs") or {}).items():
            base = _minifig_base_id(mid)
            if not base:
                continue
            idx.setdefault(base, []).append({"id": mid, "name": name})
        for fam in idx.values():
            fam.sort(key=lambda r: r["id"])
    except (OSError, ValueError):
        idx = {}
    _minifig_index = idx
    return idx


def _probe_minifig_variants(base):
    """Probe BrickLink for the family of minifig ids sharing a numeric base
    (sw0574, sw0574a, sw0574b…). BrickLink exposes no "variants of" API, so we
    ask for each candidate id and keep the ones that exist. Stops after 2
    consecutive misses — BrickLink letters its variants contiguously, and this
    also catches bases whose plain id 404s while '…a' exists (e.g. sw0001).
    Returns [{id, name, img_url}] (the family, base included)."""
    found, misses = [], 0
    candidates = [base] + [base + chr(c) for c in range(ord("a"), ord("h"))]
    for cand in candidates:
        info = _bricklink_minifig_lookup(cand)
        if info:
            found.append({"id": cand, "name": info.get("name"), "img_url": info.get("img_url")})
            misses = 0
        else:
            misses += 1
            if misses >= 2:
                break
        time.sleep(0.25)  # be polite to the BrickLink API
    return found


@app.route("/api/minifig_variants/<bl_id>")
def minifig_variants(bl_id):
    """BrickLink minifig ids sharing this id's numeric base (its variants).

    Prefers the committed offline index (minifig_variants.json) — complete,
    instant, and creds-free, so it works on Render. Falls back to a live
    BrickLink probe (cached in .bl_minifig_variants.json with a TTL) for figs not
    in the index — e.g. catalogued after the last index build. The probe is
    LOCAL-ONLY (needs BrickLink creds). `?force=1` skips the index and re-probes.
    → {base, variants:[{id,name,img_url}], cached, source:"offline"|"probe"}."""
    base = _minifig_base_id(bl_id)
    if not base:
        return jsonify({"base": None, "variants": []})
    force = request.args.get("force") == "1"

    # Offline index first — authoritative when the base is present.
    if not force:
        fam = _load_minifig_index().get(base)
        if fam:
            variants = [{"id": r["id"], "name": r["name"],
                         "img_url": _bl_minifig_img_url(r["id"])} for r in fam]
            return jsonify({"base": base, "variants": variants,
                            "cached": True, "source": "offline"})

    rec = _load_meta(BL_MINIFIG_VARIANTS_PATH).get(base)
    fresh = False
    if rec and not force:
        try:
            ts = datetime.datetime.fromisoformat(rec.get("fetched_at", ""))
            fresh = (datetime.datetime.now() - ts).days < _VARIANT_TTL_DAYS
        except (TypeError, ValueError):
            fresh = False
    if rec and fresh:
        return jsonify({"base": base, "variants": rec.get("variants", []),
                        "cached": True, "source": "probe"})

    variants = _probe_minifig_variants(base)
    # A transient BrickLink outage returns nothing — don't clobber a good cache.
    if not variants and rec:
        return jsonify({"base": base, "variants": rec.get("variants", []),
                        "cached": True, "source": "probe"})
    with _meta_lock:
        cache = _load_meta(BL_MINIFIG_VARIANTS_PATH)
        cache[base] = {"variants": variants,
                       "fetched_at": datetime.datetime.now().isoformat(timespec="seconds")}
        _save_meta(BL_MINIFIG_VARIANTS_PATH, cache)
    return jsonify({"base": base, "variants": variants, "cached": False, "source": "probe"})


def _bricklink_minifig_sets(bl_id):
    """Sets containing a minifig, looked up directly from BrickLink by its
    catalog id via the "supersets" endpoint. BrickLink's catalog is
    crowd-maintained and tends to link new figs to their sets faster than
    Rebrickable's inventory data (which can be missing/wrong for recent
    releases — the actual complaint that prompted this).

    Returns a list shaped like Rebrickable's /minifigs/<f>/sets/ results
    (set_num, name, year, set_img_url), or None on any failure. BrickLink's
    response carries no year/image, so both are filled in from the local
    catalog (by matching set_num) when available; sets missing locally —
    i.e. likely the newest ones — sort to the top.
    """
    data = bricklink_request("GET", f"/items/MINIFIG/{bl_id}/supersets")
    groups = (data or {}).get("data")
    if groups is None:
        return None

    set_nums, names, seen = [], {}, set()
    for group in groups:
        for entry in (group.get("entries") or []):
            item = entry.get("item") or {}
            set_num = item.get("no")
            if item.get("type") != "SET" or not set_num or set_num in seen:
                continue
            seen.add(set_num)
            set_nums.append(set_num)
            names[set_num] = item.get("name", "")

    local_meta = {}
    conn = local_db()
    if conn is not None and set_nums:
        try:
            placeholders = ",".join("?" for _ in set_nums)
            rows = conn.execute(
                f"SELECT set_num, name, year, img_url FROM sets WHERE set_num IN ({placeholders})",
                set_nums,
            ).fetchall()
            local_meta = {r["set_num"]: dict(r) for r in rows}
        finally:
            conn.close()

    out = []
    for set_num in set_nums:
        meta = local_meta.get(set_num)
        out.append({
            "set_num": set_num,
            "name": meta["name"] if meta else names[set_num],
            "year": meta["year"] if meta else None,
            "set_img_url": meta["img_url"] if meta else
                f"https://img.bricklink.com/ItemImage/SN/0/{set_num}.png",
        })
    out.sort(key=lambda s: (s["year"] or 9999, s["set_num"]), reverse=True)
    return out[:30]


def _local_minifig_search_by_name(name, limit=20):
    """Find Rebrickable minifigs whose names best overlap a (BrickLink) name.
    Used to surface candidates for a BrickLink minifig id; returns a ranked list
    of row dicts (best word-overlap first). Names diverge between catalogs, so
    this is intentionally a candidate list for the user to choose from, not a
    single auto-pick.
    """
    conn = local_db()
    if conn is None:
        return []
    try:
        toks = re.findall(r'[a-z0-9]+', name.lower())
        keys = sorted({w for w in toks if len(w) >= 4}, key=len, reverse=True) \
            or sorted({w for w in toks if len(w) >= 3}, key=len, reverse=True)
        if not keys:
            return []
        keys = keys[:2]
        where = " OR ".join("name LIKE ?" for _ in keys)
        rows = conn.execute(
            f"SELECT fig_num, name, num_parts, img_url FROM minifigs WHERE {where} LIMIT 300",
            [f"%{k}%" for k in keys],
        ).fetchall()
        full = set(toks)
        ranked = sorted(
            rows,
            key=lambda r: len(full & set(re.findall(r'\w+', r["name"].lower()))),
            reverse=True,
        )
        return [dict(r) for r in ranked[:limit]]
    finally:
        conn.close()


def _local_part_colors(part_num):
    """Available colors for a part (+ num_sets), from the local catalog.
    Returns a list shaped like Rebrickable's /parts/<n>/colors/ results, or None.
    """
    conn = local_db()
    if conn is None:
        return None
    try:
        rows = conn.execute(
            """
            SELECT ip.color_id AS color_id, c.name AS color_name, c.rgb AS rgb,
                   COUNT(DISTINCT inv.set_num) AS num_sets
            FROM inventory_parts ip
            JOIN inventories inv ON inv.id = ip.inventory_id
            LEFT JOIN colors c ON c.id = ip.color_id
            WHERE ip.part_num = ?
            GROUP BY ip.color_id, c.name, c.rgb
            ORDER BY num_sets DESC
            """,
            (part_num,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _local_part_color_imgs(conn, pairs):
    """Look up color-specific part images from the local catalog in one query.

    `pairs` is an iterable of (part_num, color_id). Returns {(part_num, color_id):
    img_url} for the combos that have a color-specific image in `part_colors`
    (derived at build time from the bulk dump's inventory_parts.img_url — ~94%
    coverage, zero API calls). Combos with no local image are simply absent.
    """
    out = {}
    if conn is None:
        return out
    seen = {(pn, int(cid)) for pn, cid in pairs if pn and cid is not None}
    if not seen:
        return out
    try:
        # Chunk to stay under SQLite's variable limit; the PK index makes each
        # (part_num, color_id) lookup a direct hit.
        items = list(seen)
        for i in range(0, len(items), 400):
            chunk = items[i:i + 400]
            clause = " OR ".join("(part_num = ? AND color_id = ?)" for _ in chunk)
            params = [v for pair in chunk for v in pair]
            for row in conn.execute(
                f"SELECT part_num, color_id, img_url FROM part_colors "
                f"WHERE ({clause}) AND img_url IS NOT NULL AND img_url != ''",
                params,
            ):
                out[(row["part_num"], int(row["color_id"]))] = row["img_url"]
    except sqlite3.OperationalError:
        pass
    return out


def _local_minifig_sets(fig_num):
    """Sets that contain a minifig, from the local catalog.
    Returns a list shaped like Rebrickable's /minifigs/<f>/sets/ results, or None.
    """
    conn = local_db()
    if conn is None:
        return None
    try:
        rows = conn.execute(
            """
            SELECT s.set_num, s.name AS name, s.year AS year, s.img_url AS set_img_url
            FROM inventory_minifigs im
            JOIN inventories inv ON inv.id = im.inventory_id
            JOIN sets s ON s.set_num = inv.set_num
            WHERE im.fig_num = ?
            GROUP BY s.set_num
            ORDER BY s.year DESC
            LIMIT 30
            """,
            (fig_num,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _local_minifig_parts(fig_num):
    """Parts that make up a minifig (from its own latest inventory), from the
    local catalog. Returns a list shaped like Rebrickable's /minifigs/<f>/parts/
    results (nested part/color objects), or None.
    """
    conn = local_db()
    if conn is None:
        return None
    try:
        inv = conn.execute(
            "SELECT id FROM inventories WHERE set_num = ? ORDER BY version DESC LIMIT 1",
            (fig_num,),
        ).fetchone()
        if inv is None:
            return []
        rows = conn.execute(
            """
            SELECT ip.part_num, p.name AS part_name, ip.img_url AS part_img,
                   ip.color_id, c.name AS color_name, c.rgb AS rgb, ip.quantity
            FROM inventory_parts ip
            LEFT JOIN parts p ON p.part_num = ip.part_num
            LEFT JOIN colors c ON c.id = ip.color_id
            WHERE ip.inventory_id = ?
            ORDER BY ip.quantity DESC
            """,
            (inv["id"],),
        ).fetchall()
        return [{
            "part": {
                "part_num": r["part_num"],
                "name": r["part_name"],
                "part_img_url": r["part_img"],
            },
            "color": {
                "id": r["color_id"],
                "name": r["color_name"],
                "rgb": r["rgb"],
            },
            "quantity": r["quantity"],
        } for r in rows]
    finally:
        conn.close()

# ── Rate Limiter for Rebrickable API (60 req/min = 1 req/sec) ──────────────────
# IMPORTANT: This limiter is only correct when the app runs as a SINGLE process
# (gunicorn --workers 1) with multiple threads. Each thread atomically reserves a
# 1-second slot under a shared lock, so outbound requests are globally spaced ≥1s
# apart and never exceed 60/min. Running multiple workers would give each its own
# limiter and multiply the real rate — do not raise --workers above 1.
RB_MIN_INTERVAL = 1.0  # seconds between requests (60 req/min)
_rb_lock = threading.Lock()
_rb_next_slot = 0.0          # monotonic time the next request may be sent
_rb_window_start = 0.0       # wall-clock start of current logging window
_rb_window_count = 0         # requests sent in current logging window


def throttle_rebrickable_request():
    """Globally space Rebrickable requests ≥1s apart across all threads.

    Each caller reserves the next available time slot under a lock (fast), then
    sleeps until that slot WITHOUT holding the lock, so threads don't block each
    other while waiting — they just queue up one slot apart.
    """
    global _rb_next_slot, _rb_window_start, _rb_window_count

    with _rb_lock:
        now = time.monotonic()
        slot = max(now, _rb_next_slot)
        _rb_next_slot = slot + RB_MIN_INTERVAL
        wait = slot - now

        # Per-minute counter purely for log visibility
        wall = time.time()
        if wall - _rb_window_start >= 60:
            _rb_window_start = wall
            _rb_window_count = 0
        _rb_window_count += 1
        count = _rb_window_count

    if wait > 0:
        print(f"⏳ Rate limit: waiting {wait:.2f}s before next Rebrickable request ({count}/60 this minute)")
        time.sleep(wait)

def rebrickable_get(endpoint, params=None):
    """Make a throttled GET request to Rebrickable API"""
    throttle_rebrickable_request()
    try:
        # Handle both full URLs (from pagination) and endpoint paths
        if endpoint.startswith("http"):
            url = endpoint
            # Pagination URLs already include params
            resp = http.get(url, timeout=10)
        else:
            url = f"{RB_BASE}{endpoint}"
            resp = http.get(url, params=params or {}, timeout=10)
        return resp
    except Exception as e:
        print(f"⚠ Rebrickable request error: {e}")
        return None


def _rb_collect(endpoint, params=None, max_pages=200):
    """Page through a Rebrickable list endpoint (throttled, page_size 100).

    Returns (results, error_status): all result items accumulated before the
    first failed page, and that failure's HTTP status (None if every page
    succeeded). A first-page failure therefore comes back as ([], status), a
    mid-pagination failure as a partial list — callers decide which they
    tolerate. max_pages is a runaway-pagination safety cap (~20k items).
    """
    results = []
    base = dict(params or {}, key=API_KEY, page_size=100)
    for page in range(1, max_pages + 1):
        resp = rebrickable_get(endpoint, params=dict(base, page=page))
        if resp is None or resp.status_code != 200:
            return results, (resp.status_code if resp is not None else 502)
        data = resp.json()
        results.extend(data.get("results", []))
        if not data.get("next"):
            break
    return results, None


# Fallback color list for when APIs are down
FALLBACK_COLORS = [
    {"id": 1, "name": "White"}, {"id": 2, "name": "Tan"}, {"id": 3, "name": "Light Gray"},
    {"id": 4, "name": "Dark Gray"}, {"id": 5, "name": "Black"}, {"id": 6, "name": "Dark Red"},
    {"id": 7, "name": "Red"}, {"id": 8, "name": "Dark Orange"}, {"id": 9, "name": "Orange"},
    {"id": 10, "name": "Yellow"}, {"id": 11, "name": "Dark Tan"}, {"id": 12, "name": "Dark Green"},
    {"id": 13, "name": "Green"}, {"id": 14, "name": "Dark Blue"}, {"id": 15, "name": "Blue"},
    {"id": 16, "name": "Dark Purple"}, {"id": 17, "name": "Purple"}, {"id": 18, "name": "Dark Pink"},
    {"id": 19, "name": "Pink"}, {"id": 20, "name": "Dark Brown"}, {"id": 21, "name": "Brown"},
    {"id": 22, "name": "Reddish Brown"}, {"id": 23, "name": "Trans-Black"},
    {"id": 24, "name": "Trans-Red"}, {"id": 25, "name": "Trans-Orange"},
    {"id": 26, "name": "Trans-Yellow"}, {"id": 27, "name": "Trans-Clear"},
    {"id": 28, "name": "Trans-Light Blue"}, {"id": 29, "name": "Trans-Blue"},
    {"id": 30, "name": "Trans-Green"}, {"id": 31, "name": "Trans-Brown"},
    {"id": 32, "name": "Trans-Bright Green"}, {"id": 33, "name": "Flat Silver"},
    {"id": 34, "name": "Chrome Silver"}, {"id": 35, "name": "Pearl Gold"},
    {"id": 36, "name": "Pearl Dark Gray"}, {"id": 37, "name": "Pearl Light Gray"},
    {"id": 38, "name": "Light Bluish Gray"}, {"id": 39, "name": "Dark Bluish Gray"},
    {"id": 40, "name": "Sand Green"}, {"id": 41, "name": "Medium Orange"},
    {"id": 42, "name": "Trans-Neon Orange"}, {"id": 43, "name": "Trans-Neon Green"},
    {"id": 44, "name": "Chrome Gold"}, {"id": 45, "name": "Chrome Black"}
]

# BrickLink OAuth1 helper
def bricklink_request(method, endpoint):
    """Make authenticated request to BrickLink API"""
    auth = OAuth1(
        BL_CONSUMER_KEY,
        BL_CONSUMER_SECRET,
        BL_TOKEN,
        BL_TOKEN_SECRET
    )
    url = f"{BL_BASE}{endpoint}"
    try:
        resp = http.request(method, url, auth=auth, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"BrickLink API error: {e}")
        return None


# App version = short content-hash of the page template. Changes whenever the
# app code (index.html) changes — locally on edit, on Render per deploy — so the
# client can detect "new version deployed" and auto-reload. Cached by mtime so we
# don't re-hash on every request.
_INDEX_PATH = os.path.join(_HERE, "templates", "index.html")
_version_cache = {"mtime": None, "hash": "0"}


def _app_version():
    try:
        mt = os.path.getmtime(_INDEX_PATH)
    except OSError:
        return "0"
    if _version_cache["mtime"] != mt:
        try:
            with open(_INDEX_PATH, "rb") as f:
                _version_cache["hash"] = hashlib.md5(f.read()).hexdigest()[:12]
            _version_cache["mtime"] = mt
        except OSError:
            pass
    return _version_cache["hash"]


@app.route("/")
def index():
    resp = make_response(render_template("index.html", app_version=_app_version()))
    # The HTML shell is the app — never let a browser/proxy serve a stale copy.
    # JS/CSS are inline here, so a cached document = a cached app. Force revalidation.
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


@app.route("/api/version")
def app_version():
    """Current app-code version (page content hash) for the client update check."""
    return jsonify({"version": _app_version()})


# ── PWA: serve the service worker at the root (so its scope covers the whole
#    app) and the web manifest with the right MIME type. ──
@app.route("/sw.js")
def service_worker():
    resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"      # always re-check the SW for updates
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.route("/manifest.webmanifest")
def web_manifest():
    return send_from_directory(app.static_folder, "manifest.webmanifest",
                               mimetype="application/manifest+json")


@app.route("/api/partlists")
def get_partlists():
    try:
        resp = rebrickable_get(
            f"/users/{USER_TOKEN}/partlists/",
            params={"key": API_KEY}
        )
        # If rate limited, preserve the 429 status for frontend to detect
        if resp.status_code in [429, 503]:
            return jsonify({"results": [], "error": "Rate limited or service unavailable"}), resp.status_code
        resp.raise_for_status()
        return jsonify(resp.json()), resp.status_code
    except Exception as e:
        print(f"⚠ Error fetching partlists: {e}")
        # Return 503 for other errors so frontend knows something went wrong
        return jsonify({"results": [], "error": str(e)}), 503


# ── Retirement dates (Brick Tap community sheet → retirement_sets.json) ───────
# The JSON is committed (built by refresh_retirement.py), so it's available on
# Render too; only the refresh itself is LOCAL-ONLY (needs openpyxl + writes).
RETIREMENT_PATH = os.path.join(_HERE, "retirement_sets.json")
_RETIREMENT_CACHE = {"mtime": None, "data": None}


@app.route("/api/retirement")
def get_retirement():
    """Full retirement dataset; the client filters in memory (like parts_all)."""
    try:
        mtime = os.path.getmtime(RETIREMENT_PATH)
        if _RETIREMENT_CACHE["mtime"] != mtime:
            with open(RETIREMENT_PATH) as f:
                _RETIREMENT_CACHE["data"] = json.load(f)
            _RETIREMENT_CACHE["mtime"] = mtime
        data = dict(_RETIREMENT_CACHE["data"])
    except (OSError, ValueError):
        data = {"updated": "", "sets": []}
    data["can_refresh"] = not IS_RENDER
    return jsonify(data), 200


@app.route("/api/retirement/refresh", methods=["POST"])
def retirement_refresh():
    if IS_RENDER:
        return jsonify({"error": "Refresh runs on the local instance only"}), 403
    try:
        from refresh_retirement import refresh
        out = refresh()
        return jsonify({"updated": out["updated"], "count": out["count"]}), 200
    except ImportError:
        return jsonify({"error": "openpyxl not installed (see refresh_retirement.py)"}), 501
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/catalog/status")
def catalog_status():
    """Offline-catalog freshness + refresh capability (for the scan-screen footer)."""
    present = os.path.exists(LOCAL_DB_PATH)
    info = {
        "present": present,
        "can_refresh": not IS_RENDER,
        "running": _catalog_state["running"],
        "last_result": _catalog_state["last_result"],
    }
    if present:
        mt = os.path.getmtime(LOCAL_DB_PATH)
        dt = datetime.datetime.fromtimestamp(mt)
        info["last_updated_iso"] = dt.isoformat(timespec="seconds")
        # e.g. "May 28, 2026 at 3:30 PM" (strip leading zero from hour portably)
        info["last_updated_human"] = dt.strftime("%b %d, %Y at %I:%M %p").replace(" 0", " ")
        info["db_size_mb"] = round(os.path.getsize(LOCAL_DB_PATH) / 1_000_000, 1)
    # Date of the underlying Rebrickable data, if we have a manifest.
    try:
        with open(CATALOG_MANIFEST_PATH) as f:
            man = json.load(f)
        info["data_date"] = man.get("inventory_parts", {}).get("last_modified")
    except (OSError, ValueError):
        pass
    # Last time the refresh script ran (written at the top of every run).
    try:
        with open(CATALOG_LAST_CHECKED_PATH) as f:
            iso = f.read().strip()
        dt_checked = datetime.datetime.fromisoformat(iso)
        info["last_checked_iso"] = iso
        info["last_checked_human"] = dt_checked.strftime("%b %d, %Y at %I:%M %p").replace(" 0", " ")
    except (OSError, ValueError):
        pass
    # What the most recent update added/removed (parts/minifigs/sets), if recorded.
    try:
        with open(CATALOG_CHANGES_PATH) as f:
            info["last_changes"] = json.load(f)
    except (OSError, ValueError):
        pass
    return jsonify(info)


@app.route("/api/catalog/refresh", methods=["POST"])
def catalog_refresh():
    """Kick off a manual catalog refresh in the background (local dev only)."""
    if IS_RENDER:
        return jsonify({"error": "Refresh is only available on the local dev instance"}), 403

    with _catalog_lock:
        if _catalog_state["running"]:
            return jsonify({"status": "running"}), 202
        _catalog_state["running"] = True
        _catalog_state["last_result"] = None

    force = bool((request.get_json(silent=True) or {}).get("force"))

    def _worker():
        try:
            import refresh_catalog
            result = refresh_catalog.run(force=force)
        except Exception as e:
            print(f"⚠ Catalog refresh error: {e}")
            result = {"ok": False, "changed": False, "message": str(e), "updated": []}
        with _catalog_lock:
            _catalog_state["running"] = False
            _catalog_state["last_result"] = result

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"status": "started"}), 202


def _all_colors():
    """The full color list: local catalog → live Rebrickable → FALLBACK_COLORS.
    Name-sorted, cached 1 hour. Shared by /api/colors and the CSV importer."""
    now = time.time()
    if COLORS_CACHE["data"] and (now - COLORS_CACHE["timestamp"]) < COLORS_CACHE_DURATION:
        return COLORS_CACHE["data"]

    # Prefer the local catalog: complete (~275 colors) and instant, no Rebrickable
    # quota. Falls back to the live API + FALLBACK_COLORS only when the DB is absent.
    all_colors = _local_all_colors() or []
    if not all_colors:
        try:
            url = f"{RB_BASE}/lego/colors/"
            while url:
                resp = rebrickable_get(url, params={"key": API_KEY, "page_size": 200})
                if resp is None or resp.status_code in (429, 503):
                    print(f"⚠ Rebrickable colors unavailable, using fallback")
                    all_colors = FALLBACK_COLORS
                    break
                resp.raise_for_status()
                data = resp.json()
                all_colors.extend(data.get("results", []))
                url = data.get("next")
        except Exception as e:
            print(f"⚠ Rebrickable colors error: {e}, using fallback")
            all_colors = FALLBACK_COLORS

    if not all_colors:
        all_colors = FALLBACK_COLORS

    all_colors = sorted(all_colors, key=lambda c: c["name"])
    COLORS_CACHE["data"] = all_colors
    COLORS_CACHE["timestamp"] = now
    return all_colors


@app.route("/api/colors")
@app.route("/api/colors-hybrid")   # legacy alias (older cached clients)
def get_colors():
    """Full color list with 1-hour cache (local catalog → live API → fallback)."""
    return jsonify(_all_colors())


def _brk_color_id(color_id_str):
    """Convert Brickognize 'color-N' → Rebrickable color id string 'N'."""
    if color_id_str and color_id_str.startswith("color-"):
        return color_id_str[len("color-"):]
    return None


def _brickognize_search(filename, img_bytes, content_type):
    """POST an image to Brickognize's internal search endpoint (it returns
    server-side candidate_colors, unlike the public /predict/)."""
    resp = http.post(
        "https://api.brickognize.com/internal/search/",
        params={"external_catalogs": "bricklink", "predict_color": "true"},
        files={"query_image": (filename, img_bytes, content_type)},
        headers={"Origin": "https://brickognize.com", "Referer": "https://brickognize.com/"},
        timeout=30,
    )
    return resp.json()


def _items_from_detected(detected, max_candidates=None):
    """Convert one Brickognize detected_item into our items list: strip catalog
    prefixes, resolve BrickLink→Rebrickable ids, attach images + candidate colors."""
    candidates = detected.get("candidate_items", [])
    if max_candidates:
        candidates = candidates[:max_candidates]
    items = []
    for ci in candidates:
        raw_id = ci.get("id", "")
        item_type = "minifig" if ci.get("type") in ("minifig", "fig") else "part"
        bl_id = raw_id.replace("part-", "").replace("minifig-", "").replace("fig-", "")
        ext = next((e for e in ci.get("external_items", [])
                    if e.get("catalog_name") == "bricklink"), {})
        bl_external_id = ext.get("external_id", bl_id)

        # Resolve BrickLink ID → Rebrickable ID
        rb_id = bl_id
        bl_only = False
        if item_type == "minifig":
            name = ci.get("name", "")
            resolved = None
            # Prefer the offline catalog (no API quota); fall back to the live API.
            local_fig = _local_resolve_minifig(name)
            if local_fig:
                resolved = local_fig["fig_num"]
            else:
                try:
                    # Rebrickable doesn't support bricklink_id filtering for minifigs.
                    # Strip color/variant suffix (after " - " or "(") for a cleaner search term,
                    # then pick the result with the most word overlap against the full name.
                    search_name = re.split(r' - | \(', name)[0].strip() if name else ""
                    if search_name:
                        rb_resp = http.get(
                            f"{RB_BASE}/lego/minifigs/",
                            params={"key": API_KEY, "search": search_name, "page_size": 8},
                            timeout=5,
                        )
                        if rb_resp.status_code == 200:
                            results = rb_resp.json().get("results", [])
                            if results:
                                full_words = set(re.findall(r'\w+', name.lower()))
                                def _overlap(r):
                                    return len(full_words & set(re.findall(r'\w+', r['name'].lower())))
                                best = max(results, key=_overlap)
                                # Only trust a strong match — a weak single-word
                                # overlap binds the wrong fig (see _MINIFIG_MATCH_MIN).
                                if _minifig_match_score(name, best['name']) >= _MINIFIG_MATCH_MIN:
                                    resolved = best['set_num']
                except Exception:
                    pass
            if resolved:
                rb_id = resolved
            else:
                # No confident Rebrickable match — identify by the BrickLink id so
                # the owned-collection key isn't bound to a wrong fig (e.g. a
                # brand-new fig Rebrickable hasn't catalogued). Mirrors the
                # bl-only search path (openMinifigFromBlOnly).
                rb_id = bl_external_id
                bl_only = True
            img_url = f"https://img.bricklink.com/ItemImage/MN/0/{bl_external_id}.png"
        else:
            # Prefer the offline catalog (no API quota); fall back to the live API.
            local_part = _local_resolve_part(bl_external_id)
            if local_part:
                rb_id = local_part["part_num"]
                img_url = local_part["img_url"] or \
                    f"https://img.bricklink.com/ItemImage/PN/0/{bl_external_id}.png"
            else:
                rb_part = None
                try:
                    rb_resp = http.get(
                        f"{RB_BASE}/lego/parts/",
                        params={"key": API_KEY, "bricklink_id": bl_external_id},
                        timeout=5,
                    )
                    if rb_resp.status_code == 200:
                        results = rb_resp.json().get("results", [])
                        if results:
                            rb_part = results[0]
                            rb_id = rb_part["part_num"]
                except Exception:
                    pass
                # Use Rebrickable image if available, otherwise BrickLink
                if rb_part and rb_part.get("part_img_url"):
                    img_url = rb_part["part_img_url"]
                else:
                    img_url = f"https://img.bricklink.com/ItemImage/PN/0/{bl_external_id}.png"

        # Build candidate_colors with Rebrickable IDs (parts only).
        # Brickognize's numeric color ids are BrickLink-namespaced (e.g.
        # color-156 = Medium Azure ≠ Rebrickable 156), so resolve the correct
        # Rebrickable id by NAME via the local catalog; fall back to the raw
        # stripped id only when the name can't be resolved.
        rb_colors = []
        if item_type != "minifig":
            for c in ci.get("candidate_colors", []):
                cname = c.get("name", "")
                c_rb_id = _local_color_id_by_name(cname)
                if c_rb_id is None:
                    c_rb_id = _brk_color_id(c.get("id", ""))
                if c_rb_id:
                    rb_colors.append({"id": str(c_rb_id), "name": cname})

        item = {
            "id": rb_id,
            "bl_id": bl_external_id,
            "name": ci.get("name", ""),
            "img_url": img_url,
            "external_sites": [{"name": "bricklink", "url": ext.get("url", "")}] if ext else [],
            "type": item_type,
            "score": ci.get("score", 0),
        }
        if bl_only:
            item["bl_only"] = True   # no Rebrickable catalog entry; key by bl_id
        if rb_colors:
            item["candidate_colors"] = rb_colors
        items.append(item)

    # Brickognize predicts the scanned object's colour but only attaches the
    # candidate_colors to some part guesses (e.g. the 2x2 tile, not a mis-ranked
    # 6x6). The object's colour is the same whichever part it guesses, so share
    # the first non-empty colour shortlist with every item that lacks one.
    # Otherwise the colour matcher falls back to that part's full palette and can
    # pick a wrong nearby colour the object isn't (e.g. azure → Dark Turquoise
    # when the mis-ranked part doesn't even come in Medium Azure).
    shared_colors = next((it["candidate_colors"] for it in items
                          if it.get("candidate_colors")), None)
    if shared_colors:
        for it in items:
            if not it.get("candidate_colors"):
                it["candidate_colors"] = shared_colors

    return items


@app.route("/api/identify", methods=["POST"])
def identify():
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400
    image = request.files["image"]
    img_bytes = image.read()
    try:
        idata = _brickognize_search(image.filename, img_bytes, image.content_type)

        # Convert internal format → our existing format
        detected = (idata.get("detected_items") or [{}])[0]
        bb_list = detected.get("bounding_boxes") or []
        bounding_box = bb_list[0] if bb_list else {}
        items = _items_from_detected(detected)

        data = {
            "listing_id": idata.get("id", ""),
            "bounding_box": bounding_box,
            "items": items,
        }

        with open('/tmp/brk_full.json', 'w') as f:
            json.dump(data, f, indent=2)
        return jsonify(data), 200
    except requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502


def _bbox_iou(a, b):
    ix = max(0, min(a["right"], b["right"]) - max(a["left"], b["left"]))
    iy = max(0, min(a["lower"], b["lower"]) - max(a["upper"], b["upper"]))
    inter = ix * iy
    union = ((a["right"] - a["left"]) * (a["lower"] - a["upper"])
             + (b["right"] - b["left"]) * (b["lower"] - b["upper"]) - inter)
    return inter / union if union > 0 else 0


def _find_blobs(img, bg, max_blobs=8):
    """Pieces Brickognize's detector never fired on: connected components of
    not-background pixels on a coarse grid (already-masked spots read as
    background, so they're skipped automatically). Returns full-res bboxes,
    biggest first."""
    from collections import deque
    w, h = img.size
    scale = max(1, w // 320)
    small = img.resize((max(1, w // scale), max(1, h // scale)))
    sw, sh = small.size
    px = small.load()
    grid = bytearray(sw * sh)
    for y in range(sh):
        row = y * sw
        for x in range(sw):
            r, g, b = px[x, y][:3]
            if abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > 60:
                grid[row + x] = 1
    blobs = []
    seen = bytearray(sw * sh)
    for i in range(sw * sh):
        if not grid[i] or seen[i]:
            continue
        q = deque([i])
        seen[i] = 1
        x0 = x1 = i % sw
        y0 = y1 = i // sw
        n = 0
        while q:
            j = q.popleft()
            n += 1
            xj, yj = j % sw, j // sw
            x0 = min(x0, xj); x1 = max(x1, xj)
            y0 = min(y0, yj); y1 = max(y1, yj)
            for dj in (-1, 1, -sw, sw):
                k = j + dj
                if dj in (-1, 1) and k // sw != yj:
                    continue   # row wrap
                if 0 <= k < sw * sh and grid[k] and not seen[k]:
                    seen[k] = 1
                    q.append(k)
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        # Too small = noise/shadow speck; too large = busy background, not a piece.
        if n >= 25 and bw >= 4 and bh >= 4 and bw * bh < 0.5 * sw * sh:
            blobs.append({"left": x0 * scale, "upper": y0 * scale,
                          "right": (x1 + 1) * scale, "lower": (y1 + 1) * scale,
                          "image_width": w, "image_height": h, "_n": n})
    blobs.sort(key=lambda b: -b.pop("_n"))
    return blobs[:max_blobs]


@app.route("/api/identify_multi", methods=["POST"])
def identify_multi():
    """Bulk scan: identify every piece in one photo. Brickognize only returns one
    detection per image, so loop: detect the most prominent piece, paint over its
    bounding box with the background colour, resubmit — until nothing confident
    remains. Returns regions in the (EXIF-upright) image's coordinate space."""
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400
    try:
        from PIL import Image, ImageDraw, ImageOps, ImageStat
    except ImportError:
        return jsonify({"error": "Multi-piece scan requires Pillow on the server"}), 501
    try:
        img = Image.open(request.files["image"].stream)
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception:
        return jsonify({"error": "Could not read image"}), 400

    # Downscale like the client does for single scans — keeps each round fast.
    w, h = img.size
    if w > 1280:
        img = img.resize((1280, round(h * 1280 / w)))
        w, h = img.size

    # Background colour = median of the border strips; it fills the masks that
    # hide already-identified pieces from the next round.
    edge = max(2, min(w, h) // 50)
    strips = [img.crop((0, 0, w, edge)), img.crop((0, h - edge, w, h)),
              img.crop((0, 0, edge, h)), img.crop((w - edge, 0, w, h))]
    bg = tuple(round(sum(ImageStat.Stat(s).median[c] for s in strips) / 4)
               for c in range(3))

    MAX_PIECES = 10       # regions returned
    MAX_ROUNDS = 14       # Brickognize requests per photo (politeness cap)
    MIN_SCORE = 0.2
    MAX_DUD_STREAK = 5    # consecutive non-productive rounds before giving up
    regions = []
    debug = []
    draw = ImageDraw.Draw(img)
    base_pad = max(6, min(w, h) // 60)

    def _mask(bb, pad):
        draw.rectangle([max(0, bb["left"] - pad), max(0, bb["upper"] - pad),
                        min(w, bb["right"] + pad), min(h, bb["lower"] + pad)],
                       fill=bg)

    orig = img.copy()     # pristine copy for second-pass crops (img gets masked)
    rounds = 0            # Brickognize requests across both passes
    dud_streak = 0
    try:
        # Pass 1: Brickognize's own detector, mask-and-rescan.
        while rounds < MAX_ROUNDS and len(regions) < MAX_PIECES \
                and dud_streak < MAX_DUD_STREAK:
            rounds += 1
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            idata = _brickognize_search("multi.jpg", buf.getvalue(), "image/jpeg")
            detected = (idata.get("detected_items") or [{}])[0]
            bb_list = detected.get("bounding_boxes") or []
            bb = bb_list[0] if bb_list else None
            if not bb:
                # Detector sees nothing prominent left; pass 2 sweeps leftovers.
                debug.append({"round": rounds, "result": "no detection"})
                break
            # Brickognize reports bbox coords in ITS internal (resized) space —
            # the bbox carries that space's image_width/height. Scale to our
            # image space, or every mask/overlay lands offset toward the origin.
            sx = w / (bb.get("image_width") or w)
            sy = h / (bb.get("image_height") or h)
            bb = {"left": bb["left"] * sx, "upper": bb["upper"] * sy,
                  "right": bb["right"] * sx, "lower": bb["lower"] * sy,
                  # our space, so the client's colour sampler scales correctly
                  "image_width": w, "image_height": h}
            items = _items_from_detected(detected, max_candidates=6)
            top = items[0] if items else {}
            hit = next((r["bounding_box"] for r in regions
                        if _bbox_iou(bb, r["bounding_box"]) > 0.5), None)
            debug.append({"round": rounds, "bbox": bb, "top": top.get("id"),
                          "score": top.get("score"), "overlap": bool(hit)})
            if hit or not items or (top.get("score") or 0) < MIN_SCORE:
                # Dud round: the detector re-found a masked spot (flat patches
                # leave detectable edges/fragments) or couldn't classify it.
                # Paint a progressively LARGER patch over the spot — same-size
                # re-masks provably don't dislodge the detector — and move on;
                # only a streak of duds ends the pass.
                dud_streak += 1
                union = bb if not hit else {
                    "left": min(bb["left"], hit["left"]),
                    "upper": min(bb["upper"], hit["upper"]),
                    "right": max(bb["right"], hit["right"]),
                    "lower": max(bb["lower"], hit["lower"]),
                }
                _mask(union, base_pad * (1 + dud_streak * 2))
                continue
            dud_streak = 0
            _mask(bb, base_pad)
            regions.append({"bounding_box": bb, "items": items})

        # Pass 2: pieces the detector never fired on. Find leftover
        # not-background blobs in the masked image and classify a tight crop of
        # each — Brickognize handles centered single-piece crops well even when
        # its detector skips them in the full frame.
        def _same_spot(a, b):
            # Intersection over the SMALLER box: catches a blob that surrounds
            # (or sits inside) an already-found region, where plain IoU stays low.
            ix = max(0, min(a["right"], b["right"]) - max(a["left"], b["left"]))
            iy = max(0, min(a["lower"], b["lower"]) - max(a["upper"], b["upper"]))
            amin = min((a["right"] - a["left"]) * (a["lower"] - a["upper"]),
                       (b["right"] - b["left"]) * (b["lower"] - b["upper"]))
            return amin > 0 and (ix * iy) / amin > 0.4

        for blob in _find_blobs(img, bg):
            if rounds >= MAX_ROUNDS or len(regions) >= MAX_PIECES:
                break
            if any(_same_spot(blob, r["bounding_box"]) for r in regions):
                continue
            mx = round((blob["right"] - blob["left"]) * 0.15)
            my = round((blob["lower"] - blob["upper"]) * 0.15)
            crop = orig.crop((max(0, blob["left"] - mx), max(0, blob["upper"] - my),
                              min(w, blob["right"] + mx), min(h, blob["lower"] + my)))
            rounds += 1
            buf = io.BytesIO()
            crop.save(buf, format="JPEG", quality=85)
            idata = _brickognize_search("crop.jpg", buf.getvalue(), "image/jpeg")
            detected = (idata.get("detected_items") or [{}])[0]
            items = _items_from_detected(detected, max_candidates=6)
            top = items[0] if items else {}
            debug.append({"round": rounds, "pass": "crop", "bbox": blob,
                          "top": top.get("id"), "score": top.get("score")})
            if items and (top.get("score") or 0) >= MIN_SCORE:
                regions.append({"bounding_box": blob, "items": items})
        # Read order beats discovery order: top-to-bottom, left-to-right.
        regions.sort(key=lambda r: (r["bounding_box"]["upper"] // 80,
                                    r["bounding_box"]["left"]))
    except requests.exceptions.RequestException as e:
        if not regions:
            return jsonify({"error": str(e)}), 502
    finally:
        # Debug trail for the last pile scan (mirrors /tmp/brk_full.json).
        try:
            with open("/tmp/brk_multi.json", "w") as f:
                json.dump(debug, f, indent=2, default=str)
        except OSError:
            pass
    return jsonify({"regions": regions, "image_width": w, "image_height": h}), 200


@app.route("/api/partlists/<int:list_id>/parts")
def get_partlist_parts(list_id):
    page = request.args.get("page", 1)
    resp = rebrickable_get(
        f"/users/{USER_TOKEN}/partlists/{list_id}/parts/",
        params={"key": API_KEY, "page_size": 50, "page": page},
    )
    if resp is None:
        return jsonify({"error": "Couldn't fetch list parts", "results": []}), 502
    data = resp.json()
    if resp.status_code == 200:
        results = data.get("results", [])
        # Color-accurate images: one batched local-catalog query for the page;
        # only combos with no local image (~6%) fall back to the per-item
        # Rebrickable lookup (in-memory cached).
        pairs = [((item.get("part") or {}).get("part_num"),
                  (item.get("color") or {}).get("id")) for item in results]
        conn = local_db()
        local_imgs = {}
        if conn is not None:
            try:
                local_imgs = _local_part_color_imgs(conn, pairs)
            finally:
                conn.close()
        for item, (part_num, color_id) in zip(results, pairs):
            img_url = local_imgs.get((part_num, int(color_id))) \
                if color_id is not None else None
            if not img_url:
                img_url = _part_color_img_url(part_num, color_id)
            if img_url:
                item["_accurate_img_url"] = img_url
    return jsonify(data), resp.status_code


@app.route("/api/partlists/<int:list_id>/parts_all")
def get_partlist_parts_all(list_id):
    """Flat, lightweight dump of an ENTIRE parts list for client-side search.

    Pages through Rebrickable (throttled via rebrickable_get). For each entry the
    color-specific image is pulled from the local catalog (`part_colors`, ~94%
    coverage, zero API calls), falling back to the generic part_img_url when the
    combo has no local image. Loading the whole list stays cheap — just the paged
    list calls plus a couple of batched local lookups, no per-part API fan-out.
    Powers the live search box in the Lists view, where the full set must be in
    memory to filter as the user types.
    """
    rows, err = _rb_collect(f"/users/{USER_TOKEN}/partlists/{list_id}/parts/")
    if err and not rows:
        return jsonify({"error": "Couldn't fetch list parts", "results": []}), err
    # On a mid-pagination failure, a partial list is better than none.
    out = []
    for it in rows:
        part = it.get("part") or {}
        color = it.get("color") or {}
        out.append({
            "part_num": part.get("part_num"),
            "name": part.get("name"),
            "img_url": part.get("part_img_url"),  # generic fallback
            "color_id": color.get("id"),
            "color_name": color.get("name"),
            "rgb": color.get("rgb"),
            "quantity": it.get("quantity") or 1,
        })

    # Overlay color-specific images from the local catalog (one batched query).
    conn = local_db()
    if conn is not None:
        try:
            imgs = _local_part_color_imgs(conn, [(p["part_num"], p["color_id"]) for p in out])
            for p in out:
                if p["color_id"] is None:
                    continue
                local_img = imgs.get((p["part_num"], int(p["color_id"])))
                if local_img:
                    p["img_url"] = local_img
        finally:
            conn.close()

    return jsonify({"results": out, "count": len(out)})


@app.route("/api/partlists/compare")
def compare_partlists():
    list_a = request.args.get("list_a", type=int)
    list_b = request.args.get("list_b", type=int)
    if not list_a or not list_b:
        return jsonify({"error": "list_a and list_b required"}), 400
    if list_a == list_b:
        return jsonify({"error": "Select two different lists"}), 400

    parts_a = _fetch_partlist_parts_map(list_a)
    parts_b = _fetch_partlist_parts_map(list_b)

    # Overlay color-specific images from the local catalog
    conn = local_db()
    if conn is not None:
        try:
            all_parts = list(parts_a.values()) + list(parts_b.values())
            imgs = _local_part_color_imgs(conn, [(p["part_num"], p["color_id"]) for p in all_parts])
            for d in (parts_a, parts_b):
                for p in d.values():
                    if p["color_id"] is not None:
                        local_img = imgs.get((p["part_num"], int(p["color_id"])))
                        if local_img:
                            p["img_url"] = local_img
        finally:
            conn.close()

    keys_a = set(parts_a)
    keys_b = set(parts_b)
    in_both, only_a, only_b = [], [], []

    for key in keys_a & keys_b:
        p = dict(parts_a[key])
        p["qty_a"] = parts_a[key]["quantity"]
        p["qty_b"] = parts_b[key]["quantity"]
        del p["quantity"]
        in_both.append(p)

    for key in keys_a - keys_b:
        p = dict(parts_a[key])
        p["qty_a"] = p.pop("quantity")
        p["qty_b"] = 0
        only_a.append(p)

    for key in keys_b - keys_a:
        p = dict(parts_b[key])
        p["qty_a"] = 0
        p["qty_b"] = p.pop("quantity")
        only_b.append(p)

    for lst in (in_both, only_a, only_b):
        lst.sort(key=lambda p: (p.get("name") or "").lower())

    return jsonify({"in_both": in_both, "only_a": only_a, "only_b": only_b})


def _fetch_partlist_parts_map(list_id):
    """Fetch an entire parts list as {(part_num, color_id): {…, quantity}}.

    Throttled paging via rebrickable_get. Shared by the list-compare and
    subtract-a-set features.
    """
    rows, _err = _rb_collect(f"/users/{USER_TOKEN}/partlists/{list_id}/parts/")
    parts = {}
    for it in rows:
        part = it.get("part") or {}
        color = it.get("color") or {}
        parts[(part.get("part_num"), color.get("id"))] = {
            "part_num": part.get("part_num"),
            "name": part.get("name"),
            "img_url": part.get("part_img_url"),
            "color_id": color.get("id"),
            "color_name": color.get("name"),
            "rgb": color.get("rgb"),
            "quantity": it.get("quantity") or 1,
        }
    return parts


def _fetch_set_parts(set_num):
    """Aggregate a set's inventory by (part_num, color_id), including spares —
    they're physical pieces in the box too. Returns
    (set_name, set_img, parts_by_key, total_pieces) or None on hard failure.
    Shared by set-overlap and the subtract-a-set record."""
    set_name, set_img = set_num, None
    info_resp = rebrickable_get(f"/lego/sets/{set_num}/", params={"key": API_KEY})
    if info_resp is not None and info_resp.status_code == 200:
        info = info_resp.json()
        set_name = info.get("name") or set_num
        set_img = info.get("set_img_url")
    elif info_resp is not None and info_resp.status_code == 404:
        return None

    rows, err = _rb_collect(f"/lego/sets/{set_num}/parts/")
    if err and not rows:
        return None
    set_parts = {}
    set_total = 0
    for it in rows:
        part = it.get("part") or {}
        color = it.get("color") or {}
        key = (part.get("part_num"), color.get("id"))
        qty = it.get("quantity") or 0
        set_total += qty
        if key in set_parts:
            set_parts[key]["quantity"] += qty
        else:
            set_parts[key] = {
                "part_num": part.get("part_num"),
                "name": part.get("name"),
                "img_url": part.get("part_img_url"),
                "color_id": color.get("id"),
                "color_name": color.get("name"),
                "rgb": color.get("rgb"),
                "quantity": qty,
            }
    return set_name, set_img, set_parts, set_total


def _normalize_set_num(set_num):
    """Rebrickable set numbers carry a variant suffix (e.g. '75060-1'). Append
    '-1' to a bare number so '75060' resolves like the rest of the app does."""
    set_num = (set_num or "").strip()
    if set_num and "-" not in set_num:
        set_num += "-1"
    return set_num


@app.route("/api/partlists/<int:list_id>/set_overlap")
def partlist_set_overlap(list_id):
    """Preview how many pieces a given SET would remove from a parts list.

    For every (part_num, color_id) the set contributes that the list also needs,
    the removable quantity is min(set_qty, list_qty). Returns the per-line
    breakdown plus totals; nothing is mutated (the apply step is a separate POST).
    """
    set_num = _normalize_set_num(request.args.get("set_num", ""))
    if not set_num:
        return jsonify({"error": "set_num required"}), 400

    fetched = _fetch_set_parts(set_num)
    if fetched is None:
        return jsonify({"error": f"Couldn't fetch set {set_num}"}), 502
    set_name, set_img, set_parts, set_total = fetched

    list_parts = _fetch_partlist_parts_map(list_id)

    items = []
    total_remove = 0
    for key, sp in set_parts.items():
        lp = list_parts.get(key)
        if not lp:
            continue
        list_qty = lp["quantity"]
        remove_qty = min(sp["quantity"], list_qty)
        if remove_qty <= 0:
            continue
        total_remove += remove_qty
        items.append({
            "part_num": lp["part_num"],
            "name": lp["name"] or sp["name"],
            "img_url": lp["img_url"] or sp["img_url"],
            "color_id": lp["color_id"],
            "color_name": lp["color_name"] or sp["color_name"],
            "rgb": lp["rgb"] or sp["rgb"],
            "list_qty": list_qty,
            "set_qty": sp["quantity"],
            "remove_qty": remove_qty,
            "remaining_qty": list_qty - remove_qty,
        })

    # Overlay color-specific images from the local catalog (one batched query).
    conn = local_db()
    if conn is not None:
        try:
            imgs = _local_part_color_imgs(conn, [(p["part_num"], p["color_id"]) for p in items])
            for p in items:
                if p["color_id"] is not None:
                    local_img = imgs.get((p["part_num"], int(p["color_id"])))
                    if local_img:
                        p["img_url"] = local_img
        finally:
            conn.close()

    items.sort(key=lambda p: (p.get("name") or "").lower())
    cleared = sum(1 for p in items if p["remaining_qty"] == 0)

    return jsonify({
        "set_num": set_num,
        "set_name": set_name,
        "set_img": set_img,
        "items": items,
        "totals": {
            "lines": len(items),
            "pieces": total_remove,
            "cleared": cleared,
            "set_pieces": set_total,
        },
    })


@app.route("/api/partlists/<int:list_id>/subtract_set", methods=["POST"])
def partlist_subtract_set(list_id):
    """Apply a set-overlap preview: decrement each line by remove_qty (PUT the
    new quantity, or DELETE when it reaches 0). Trusts the client-supplied
    new_qty from the preview the user just confirmed."""
    data = request.json or {}
    items = data.get("items") or []
    if not items:
        return jsonify({"error": "No items to subtract"}), 400

    updated, deleted, removed_pieces = 0, 0, 0
    failed = []
    removed_map = {}     # "part|color" → pieces actually pulled into the list
    for it in items:
        part_num = it.get("part_num")
        color_id = it.get("color_id")
        new_qty = int(it.get("new_qty", 0))
        remove_qty = int(it.get("remove_qty", 0))
        if part_num is None or color_id is None:
            continue
        item_url = f"{RB_BASE}/users/{USER_TOKEN}/partlists/{list_id}/parts/{part_num}/{color_id}/"
        throttle_rebrickable_request()
        try:
            if new_qty <= 0:
                resp = http.delete(item_url, params={"key": API_KEY}, timeout=10)
                ok = resp.status_code in (200, 204)
                if ok:
                    deleted += 1
            else:
                resp = http.put(item_url, params={"key": API_KEY},
                                    data={"quantity": new_qty}, timeout=10)
                ok = resp.status_code == 200
                if ok:
                    updated += 1
            if ok:
                removed_pieces += remove_qty
                if remove_qty > 0:
                    removed_map[f"{part_num}|{color_id}"] = remove_qty
            else:
                failed.append({"part_num": part_num, "color_id": color_id,
                               "status": resp.status_code})
        except Exception as e:
            failed.append({"part_num": part_num, "color_id": color_id, "error": str(e)})

    # Record what this set contributed to the list (subtracted) vs. what's left
    # over (spare) — a collapsible card on the Part Lists tab. Built in the
    # background (re-fetches the set's full inventory); seed a placeholder now.
    record_key = None
    set_num = _normalize_set_num(data.get("set_num", ""))
    if set_num and removed_map:
        record_key = f"{set_num}__{list_id}"
        with _meta_lock:
            recs = _load_meta(SUBTRACT_RECORDS_PATH)
            recs[record_key] = {
                **(recs.get(record_key) or {}),
                "key": record_key,
                "set_num": set_num,
                "set_name": data.get("set_name") or set_num,
                "list_id": list_id,
                "list_name": data.get("list_name") or f"list {list_id}",
                "ts": time.time(),
                "building": True,
            }
            _save_meta(SUBTRACT_RECORDS_PATH, recs)
        threading.Thread(
            target=_build_subtract_record,
            args=(record_key, set_num, list_id, data.get("list_name") or f"list {list_id}", removed_map),
            daemon=True,
        ).start()

    return jsonify({
        "updated": updated,
        "deleted": deleted,
        "removed_pieces": removed_pieces,
        "failed": failed,
        "record_key": record_key,
    }), (200 if not failed else 207)


def _rb_part_to_bl(conn, part_num):
    """Rebrickable part_num → BrickLink item id (reverse of bl_aliases). Falls
    back to the part_num itself (identical for most standard parts)."""
    if conn is not None and part_num:
        try:
            row = conn.execute(
                "SELECT bl_id FROM bl_aliases WHERE part_num = ? ORDER BY length(bl_id), bl_id LIMIT 1",
                (part_num,),
            ).fetchone()
            if row:
                return row[0]
        except sqlite3.OperationalError:
            pass
    return part_num


def _rb_color_to_bl(conn, color_id):
    """Rebrickable color id → BrickLink color id (bl_colors), or None if unmapped."""
    if conn is not None and color_id is not None:
        try:
            row = conn.execute("SELECT bl_id FROM bl_colors WHERE rb_id = ?", (color_id,)).fetchone()
            if row:
                return row[0]
        except sqlite3.OperationalError:
            pass
    return None


@app.route("/api/partlists/<int:list_id>/bricklink_wanted")
def export_bricklink_wanted(list_id):
    """Export a parts list as a BrickLink Wanted List XML (upload format).

    Translates each Rebrickable part_num → BrickLink item id (bl_aliases) and
    color id → BrickLink color id (bl_colors). Returns {xml, item_count,
    total_qty, unmapped_colors}. Parts whose color has no BrickLink mapping are
    included without a <COLOR> (BrickLink treats as any color) and counted.
    """
    from xml.sax.saxutils import escape
    conn = local_db()
    try:
        rows, err = _rb_collect(f"/users/{USER_TOKEN}/partlists/{list_id}/parts/")
        if err:
            return jsonify({"error": "Couldn't fetch list parts from Rebrickable"}), 502
        items = [(
            (it.get("part") or {}).get("part_num"),
            (it.get("color") or {}).get("id"),
            it.get("quantity") or 1,
        ) for it in rows]

        lines = ["<INVENTORY>"]
        total_qty = 0
        unmapped_colors = 0
        for part_num, color_id, qty in items:
            if not part_num:
                continue
            bl_item = _rb_part_to_bl(conn, part_num)
            bl_color = _rb_color_to_bl(conn, color_id)
            total_qty += int(qty)
            lines.append("  <ITEM>")
            lines.append("    <ITEMTYPE>P</ITEMTYPE>")
            lines.append(f"    <ITEMID>{escape(str(bl_item))}</ITEMID>")
            if bl_color is not None:
                lines.append(f"    <COLOR>{int(bl_color)}</COLOR>")
            else:
                unmapped_colors += 1
            lines.append(f"    <MINQTY>{int(qty)}</MINQTY>")
            lines.append("  </ITEM>")
        lines.append("</INVENTORY>")

        return jsonify({
            "xml": "\n".join(lines),
            "item_count": len([i for i in items if i[0]]),
            "total_qty": total_qty,
            "unmapped_colors": unmapped_colors,
        })
    finally:
        if conn is not None:
            conn.close()


def _part_color_img_url(part_num, color_id):
    if not part_num or color_id is None:
        return None
    cache_key = (part_num, int(color_id))
    if cache_key in PART_COLOR_IMAGE_CACHE:
        return PART_COLOR_IMAGE_CACHE[cache_key]

    img_url = None
    try:
        resp = http.get(
            f"{RB_BASE}/lego/parts/{part_num}/colors/",
            params={"key": API_KEY, "page_size": 100},
            timeout=5,
        )
        if resp.status_code == 200:
            for color in resp.json().get("results", []):
                if color.get("color_id") == int(color_id):
                    img_url = color.get("part_img_url")
                    break
    except requests.exceptions.RequestException:
        img_url = None

    PART_COLOR_IMAGE_CACHE[cache_key] = img_url
    return img_url


@app.route("/api/partlists/<int:list_id>/parts/<part_num>/<int:color_id>")
def get_partlist_part(list_id, part_num, color_id):
    resp = http.get(
        f"{RB_BASE}/users/{USER_TOKEN}/partlists/{list_id}/parts/{part_num}/{color_id}/",
        params={"key": API_KEY},
    )
    if resp.status_code == 404:
        return jsonify({"quantity": 0, "_exists": False}), 200
    data = resp.json()
    data["_exists"] = True
    return jsonify(data), resp.status_code


@app.route("/api/part/<part_num>")
def get_part(part_num):
    resp = rebrickable_get(
        f"/lego/parts/{part_num}/",
        params={"key": API_KEY},
    )
    if resp is None:
        return jsonify({"error": "Failed to fetch part"}), 503
    return jsonify(resp.json()), resp.status_code


@app.route("/api/part_colors/<part_num>")
def get_part_colors(part_num):
    # Prefer the offline catalog (no API quota); fall back to the live API.
    # Empty result → part isn't in any local inventory, so try the API instead.
    local = _local_part_colors(part_num)
    if local:
        return jsonify({"count": len(local), "results": local}), 200
    resp = rebrickable_get(
        f"/lego/parts/{part_num}/colors/",
        params={"key": API_KEY, "page_size": 100},
    )
    if resp is None:
        return jsonify({"error": "Failed to fetch part colors", "results": []}), 503
    return jsonify(resp.json()), resp.status_code


@app.route("/api/part_in_lists/<part_num>/<int:color_id>")
def get_part_in_lists(part_num, color_id):
    """Fetch all lists containing a specific part/color with quantities."""
    try:
        # Get all user's lists
        lists_resp = http.get(
            f"{RB_BASE}/users/{USER_TOKEN}/partlists/",
            params={"key": API_KEY},
        )
        lists_data = lists_resp.json()
        lists = lists_data.get("results", [])

        # For each list, check if the part exists
        lists_with_part = []
        for lst in lists:
            part_resp = http.get(
                f"{RB_BASE}/users/{USER_TOKEN}/partlists/{lst['id']}/parts/{part_num}/{color_id}/",
                params={"key": API_KEY},
                timeout=5,
            )
            if part_resp.status_code == 200:
                part_data = part_resp.json()
                lists_with_part.append({
                    "list_id": lst["id"],
                    "list_name": lst["name"],
                    "quantity": part_data.get("quantity", 0)
                })

        return jsonify({"results": lists_with_part}), 200
    except Exception as e:
        print(f"Error fetching part in lists: {e}")
        return jsonify({"error": str(e), "results": []}), 500


@app.route("/api/part_bins")
def get_part_bins():
    """Full part→bin map (.part_bins.json); the client joins it onto list rows."""
    return jsonify({"bins": _load_meta(PART_BINS_PATH)})


@app.route("/api/part_bins/<path:part_num>", methods=["POST"])
def set_part_bin(part_num):
    """Set (or clear, with an empty label) the sorting-bin location of a part."""
    label = str((request.json or {}).get("bin") or "").strip()[:40]
    with _meta_lock:
        bins = _load_meta(PART_BINS_PATH)
        if label:
            bins[part_num] = label
        else:
            bins.pop(part_num, None)
        _save_meta(PART_BINS_PATH, bins)
    return jsonify({"part_num": part_num, "bin": label or None})


@app.route("/bins/print")
def bin_stickers():
    """Printable QR sticker sheet — one card per distinct bin label. Each QR
    encodes <base>/?bin=<label> (base editable on the page, since stickers must
    point at the host the PHONE uses — Tailscale — not where they're printed)."""
    bins = _load_meta(PART_BINS_PATH)
    by_label = {}
    for part_num, label in sorted(bins.items()):
        by_label.setdefault(label, []).append({"part_num": part_num, "name": None})
    conn = local_db()
    if conn is not None:
        try:
            part_nums = sorted(bins)
            names = {}
            for i in range(0, len(part_nums), 400):  # SQLite variable limit
                chunk = part_nums[i:i + 400]
                placeholders = ",".join("?" for _ in chunk)
                names.update(conn.execute(
                    f"SELECT part_num, name FROM parts WHERE part_num IN ({placeholders})",
                    chunk,
                ).fetchall())
            for parts in by_label.values():
                for p in parts:
                    p["name"] = names.get(p["part_num"])
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()
    labels = sorted(by_label.keys(), key=lambda s: (len(s), s.lower()))
    return render_template("bin_stickers.html", labels=labels, by_label=by_label)


@app.route("/api/add_part", methods=["POST"])
def add_part():
    data = request.json
    list_id = data["list_id"]
    part_num = data["part_num"]
    color_id = data["color_id"]
    quantity = int(data["quantity"])

    # Check if this part+color already exists: GET /parts/{part_num}/{color_id}/
    existing = http.get(
        f"{RB_BASE}/users/{USER_TOKEN}/partlists/{list_id}/parts/{part_num}/{color_id}/",
        params={"key": API_KEY},
    )

    print(f"[add_part] list={list_id} part={part_num} color={color_id} qty={quantity}")

    if existing.status_code == 200:
        current_qty = existing.json().get("quantity", 0)
        new_qty = current_qty + quantity
        resp = http.put(
            f"{RB_BASE}/users/{USER_TOKEN}/partlists/{list_id}/parts/{part_num}/{color_id}/",
            params={"key": API_KEY},
            data={"quantity": new_qty},
        )
        print(f"[add_part] PUT {resp.status_code}: {resp.text[:200]}")
        result = resp.json()
        result["_updated"] = True
        result["_previous_quantity"] = current_qty
        return jsonify(result), resp.status_code
    else:
        resp = http.post(
            f"{RB_BASE}/users/{USER_TOKEN}/partlists/{list_id}/parts/",
            params={"key": API_KEY},
            data={"part_num": part_num, "color_id": color_id, "quantity": quantity},
        )
        print(f"[add_part] POST {resp.status_code}: {resp.text[:200]}")
        return jsonify(resp.json()), resp.status_code


@app.route("/api/remove_part_one", methods=["POST"])
def remove_part_one():
    data = request.json
    list_id = data["list_id"]
    part_num = data["part_num"]
    color_id = data["color_id"]

    item_url = f"{RB_BASE}/users/{USER_TOKEN}/partlists/{list_id}/parts/{part_num}/{color_id}/"
    existing = http.get(item_url, params={"key": API_KEY})

    print(f"[remove_part_one] list={list_id} part={part_num} color={color_id}")

    if existing.status_code == 404:
        return jsonify({"error": "Part is not in this list.", "_previous_quantity": 0}), 404
    if existing.status_code != 200:
        return jsonify(existing.json()), existing.status_code

    current_qty = int(existing.json().get("quantity", 0))
    if current_qty <= 1:
        resp = http.delete(item_url, params={"key": API_KEY})
        if resp.status_code == 204:
            return jsonify({
                "_deleted": True,
                "_previous_quantity": current_qty,
                "quantity": 0,
            }), 200
        return jsonify(resp.json()), resp.status_code

    new_qty = current_qty - 1
    resp = http.put(item_url, params={"key": API_KEY}, data={"quantity": new_qty})
    if resp.status_code == 200:
        result = resp.json()
        result["_updated"] = True
        result["_previous_quantity"] = current_qty
        result["quantity"] = new_qty
        return jsonify(result), 200
    return jsonify(resp.json()), resp.status_code


@app.route("/api/partlists/<int:list_id>", methods=["DELETE"])
def delete_partlist(list_id):
    resp = http.delete(
        f"{RB_BASE}/users/{USER_TOKEN}/partlists/{list_id}/",
        params={"key": API_KEY},
    )
    if resp.status_code == 204:
        return '', 204
    return jsonify(resp.json()), resp.status_code


@app.route("/api/partlists", methods=["POST"])
def create_partlist():
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400
    resp = http.post(
        f"{RB_BASE}/users/{USER_TOKEN}/partlists/",
        params={"key": API_KEY},
        data={"name": name},
    )
    return jsonify(resp.json()), resp.status_code


@app.route("/api/minifig_sets/<set_num>")
def get_minifig_sets(set_num):
    # If we know the BrickLink catalog id, prefer its set linkage — it's
    # crowd-maintained and stays current for new releases, where Rebrickable's
    # inventory data can be missing or wrong (the figure itself still resolves
    # fine; it's the "appears in" list that's stale).
    bl_id = (request.args.get("bl_id") or "").strip()
    if bl_id:
        bl_sets = _bricklink_minifig_sets(bl_id)
        if bl_sets is not None:
            return jsonify({"count": len(bl_sets), "results": bl_sets, "source": "bricklink"}), 200

    # Prefer the offline catalog (no API quota); fall back to the live API.
    local = _local_minifig_sets(set_num)
    if local:
        return jsonify({"count": len(local), "results": local}), 200
    resp = http.get(
        f"{RB_BASE}/lego/minifigs/{set_num}/sets/",
        params={"key": API_KEY, "page_size": 30},
    )
    return jsonify(resp.json()), resp.status_code


@app.route("/api/minifig/<minifig_id>")
def get_minifig(minifig_id):
    is_rebrickable_format = minifig_id.startswith('fig-')

    def try_bricklink(fig_id):
        info = _bricklink_minifig_lookup(fig_id)
        if not info:
            return None
        return {
            'fig_num': fig_id,
            'name': info['name'],
            'fig_img_url': info.get('img_url') or '',
            'external_id': fig_id,
            'source': 'bricklink',
        }

    if not is_rebrickable_format:
        result = try_bricklink(minifig_id)
        if result:
            return jsonify(result), 200

    # Try Rebrickable (primary for fig- format, fallback for BL format)
    try:
        resp = http.get(
            f"{RB_BASE}/lego/minifigs/{minifig_id}/",
            params={"key": API_KEY},
        )
        if resp.status_code == 200:
            data = resp.json()
            # Surface a BrickLink id as `external_id` if Rebrickable happens to
            # include one (its minifig endpoint usually doesn't — external_ids is
            # null — but parts/some figs do; harmless when absent).
            bl = (data.get("external_ids") or {}).get("BrickLink") or {}
            ext_ids = bl.get("ext_ids") or []
            if ext_ids and not data.get("external_id"):
                data["external_id"] = ext_ids[0]
            return jsonify(data), 200
    except Exception as e:
        print(f"Rebrickable lookup error: {e}")

    if is_rebrickable_format:
        result = try_bricklink(minifig_id)
        if result:
            return jsonify(result), 200

    # Fallback for BL-format IDs: return partial data using direct image URL
    # (works even when BrickLink API is blocked, e.g. on cloud hosting)
    if not is_rebrickable_format:
        img_url = f"https://img.bricklink.com/ML/{minifig_id}.jpg"
        print(f"BrickLink API unavailable, using image fallback for {minifig_id}", file=sys.stderr)
        return jsonify({
            'fig_num': minifig_id,
            'name': minifig_id,
            'fig_img_url': img_url,
            'external_id': minifig_id,
            'source': 'fallback'
        }), 200

    return jsonify({"error": "Minifig not found"}), 404


@app.route("/api/minifiglists")
def get_minifiglists():
    # Rebrickable has no separate minifig-lists API — only a single flat
    # collection at /users/{token}/minifigs/. Return a synthetic list so the
    # frontend list-picker works without changes.
    return jsonify({"count": 1, "results": [{"id": 0, "name": "My Minifigs"}]}), 200


@app.route("/api/minifiglists", methods=["POST"])
def create_minifiglist():
    return jsonify({"error": "Rebrickable does not support multiple minifig lists"}), 400


@app.route("/api/minifiglists/<int:list_id>", methods=["DELETE"])
def delete_minifiglist(list_id):
    return jsonify({"error": "Rebrickable does not support multiple minifig lists"}), 400


# ── Owned minifigs ("My Minifigs") — a LOCAL collection. Rebrickable's
#    /users/{token}/minifigs/ is read-only (GET-only; it just aggregates the
#    minifigs inside the user's owned sets — no POST/PUT/DELETE, no per-item
#    endpoint), so there's no way to maintain an owned-minifig list on
#    Rebrickable. Instead the whole collection (quantity + condition + price +
#    display name/image) lives in a local JSON store keyed by fig_num.
#    LOCAL-ONLY: ephemeral on Render, same as the set metadata. ──

def _minifig_variant_suffix(bl_id):
    """The BrickLink variant suffix of a minifig id — the trailing letter(s)
    after the numeric part (e.g. 'sw0574a' → 'a', 'sw0574' → ''). BrickLink
    splits print/mold variants of one Rebrickable fig with these suffixes, so the
    suffix is what distinguishes two owned variants of the same fig_num."""
    m = re.match(r'^[a-z]+\d+([a-z]+)$', (bl_id or "").strip().lower())
    return m.group(1) if m else ""


def _minifig_ckey(fig_num, bl_id):
    """Collection key for an owned minifig. Variants of the same Rebrickable fig
    (distinguished only by BrickLink suffix) are tracked as separate entries: the
    base/suffix-less id keeps the bare fig_num key (backward compatible — existing
    entries are unaffected); a suffixed variant gets 'fig_num#<suffix>'."""
    suf = _minifig_variant_suffix(bl_id)
    return f"{fig_num}#{suf}" if suf else fig_num


@app.route("/api/add_minifig", methods=["POST"])
def add_minifig():
    """Add a minifig to the local owned-minifig collection (merges quantity).
    Body: {set_num (fig_num), quantity, name, img_url, bl_id}. A BrickLink id with
    a variant suffix (e.g. sw0574a) is tracked as its own entry — see
    _minifig_ckey."""
    data = request.json
    fig_num = data["set_num"]
    bl_id = (data.get("bl_id") or "").strip() or None
    key = _minifig_ckey(fig_num, bl_id)
    quantity = int(data.get("quantity", 1))
    with _meta_lock:
        coll = _load_meta(MINIFIG_COLLECTION_PATH)
        entry = coll.get(key) or {"condition": None, "price_paid": None}
        prev = int(entry.get("quantity", 0))
        entry["quantity"] = prev + quantity
        entry["fig_num"] = fig_num  # real Rebrickable fig (key may be composite)
        if data.get("name"):
            entry["name"] = data["name"]
        if data.get("img_url"):
            entry["img_url"] = data["img_url"]
        if bl_id:
            entry["bl_id"] = bl_id
        coll[key] = entry
        _save_meta(MINIFIG_COLLECTION_PATH, coll)
    return jsonify({"quantity": entry["quantity"], "_updated": prev > 0, "_previous_quantity": prev})


@app.route("/api/remove_minifig_one", methods=["POST"])
def remove_minifig_one():
    """Decrement an owned minifig by 1 (delete the entry, and its metadata, at 0).
    Body: {set_num (fig_num), bl_id?} — a suffixed bl_id targets that variant's
    own entry (see _minifig_ckey)."""
    fig_num = request.json["set_num"]
    bl_id = (request.json.get("bl_id") or "").strip() or None
    key = _minifig_ckey(fig_num, bl_id)
    with _meta_lock:
        coll = _load_meta(MINIFIG_COLLECTION_PATH)
        entry = coll.get(key)
        if not entry:
            return jsonify({"error": "Minifig is not in your collection.", "quantity": 0}), 404
        prev = int(entry.get("quantity", 0))
        if prev <= 1:
            coll.pop(key, None)
            _save_meta(MINIFIG_COLLECTION_PATH, coll)
            return jsonify({"_deleted": True, "_previous_quantity": prev, "quantity": 0})
        entry["quantity"] = prev - 1
        coll[key] = entry
        _save_meta(MINIFIG_COLLECTION_PATH, coll)
        return jsonify({"_updated": True, "_previous_quantity": prev, "quantity": prev - 1})


@app.route("/api/owned_minifigs/<fig_num>")
def owned_minifig_status(fig_num):
    """Is this minifig in the local collection? → {owned, quantity, condition,
    price_paid, bl_id}. An optional ?bl=<bl_id> selects a specific BrickLink
    variant's entry (see _minifig_ckey)."""
    bl_id = (request.args.get("bl") or "").strip() or None
    entry = _load_meta(MINIFIG_COLLECTION_PATH).get(_minifig_ckey(fig_num, bl_id))
    # If a specific bl_id was requested and the stored entry belongs to a different
    # BrickLink id (same Rebrickable fig_num, different base id — e.g. sw0060 vs
    # sw0224), don't report it as owned. Only applies when the stored entry actually
    # has a bl_id recorded; if it doesn't we can't tell, so fall through.
    if entry and bl_id and entry.get("bl_id") and \
            entry["bl_id"].lower() != bl_id.lower():
        entry = None
    if entry and int(entry.get("quantity", 0)) > 0:
        return jsonify({
            "owned": True,
            "quantity": entry.get("quantity", 1),
            "condition": entry.get("condition"),
            "price_paid": entry.get("price_paid"),
            "bl_id": entry.get("bl_id"),
        })
    return jsonify({"owned": False, "quantity": 0})


@app.route("/api/owned_minifigs/<fig_num>/meta", methods=["POST"])
def minifig_owned_meta(fig_num):
    """Save purchase metadata (condition + price paid) on an owned minifig.
    Body: {condition: "used"|"new"|null, price_paid: number|null, bl_id?}. The
    optional bl_id selects a variant's entry. No-op if the minifig isn't owned
    (metadata only attaches to a collection entry)."""
    body = request.json or {}
    bl_id = (body.get("bl_id") or "").strip() or None
    key = _minifig_ckey(fig_num, bl_id)
    clean = _clean_meta(body) or {}
    with _meta_lock:
        coll = _load_meta(MINIFIG_COLLECTION_PATH)
        entry = coll.get(key)
        if entry is None:
            return jsonify({"condition": None, "price_paid": None})
        entry["condition"] = clean.get("condition")
        entry["price_paid"] = clean.get("price_paid")
        coll[key] = entry
        _save_meta(MINIFIG_COLLECTION_PATH, coll)
    return jsonify({"condition": entry["condition"], "price_paid": entry["price_paid"]})


@app.route("/api/owned_minifigs/<fig_num>/blid", methods=["POST"])
def minifig_set_blid(fig_num):
    """Manually set/clear the BrickLink id on an owned minifig (so it can be
    priced). Body: {bl_id: "sw0530"|null}. Clears stored prices when the id
    changes so a refresh re-fetches. No-op if the minifig isn't owned."""
    bl_id = (request.json or {}).get("bl_id")
    bl_id = bl_id.strip() if isinstance(bl_id, str) and bl_id.strip() else None
    # Backfill only applies to the entry the new id keys to. A suffix-less id
    # keys to the base (fig_num) entry; a suffixed id keys to its variant entry,
    # which usually doesn't exist until "Add" creates it — then this no-ops.
    key = _minifig_ckey(fig_num, bl_id)
    with _meta_lock:
        coll = _load_meta(MINIFIG_COLLECTION_PATH)
        entry = coll.get(key)
        if entry is None:
            return jsonify({"bl_id": None})
        if entry.get("bl_id") != bl_id:
            entry["bl_id"] = bl_id
            entry["price_used"] = None
            entry["price_new"] = None
            entry["price_updated"] = None
        coll[key] = entry
        _save_meta(MINIFIG_COLLECTION_PATH, coll)
    return jsonify({"bl_id": bl_id})


@app.route("/api/owned_minifigs")
def owned_minifigs_list():
    """The local owned-minifig collection (quantity + condition + price), name-sorted."""
    coll = _load_meta(MINIFIG_COLLECTION_PATH)
    out = []
    for key, e in coll.items():
        if int(e.get("quantity", 0)) <= 0:
            continue
        # The dict key may be composite (fig_num#suffix) for a BrickLink variant;
        # expose the real Rebrickable fig_num (stored on add, or the key's stem).
        fig_num = e.get("fig_num") or key.split("#", 1)[0]
        out.append({
            "fig_num": fig_num,
            "name": e.get("name"),
            "bl_id": e.get("bl_id"),
            "num_parts": e.get("num_parts"),
            "img_url": e.get("img_url"),
            "quantity": e.get("quantity", 1),
            "condition": e.get("condition"),
            "price_paid": e.get("price_paid"),
            "price_used": e.get("price_used"),
            "price_new": e.get("price_new"),
            "price_updated": e.get("price_updated"),
        })
    out.sort(key=lambda x: (x.get("name") or x["fig_num"]).lower())
    return jsonify({"results": out, "count": len(out)})


def _bl_avg(guide, cond):
    """The positive avg_price for one condition of a _bl_sold_price guide,
    rounded to cents, or None."""
    try:
        v = (guide.get(cond) or {}).get("avg_price")
        f = float(v)
        return round(f, 2) if f > 0 else None
    except (TypeError, ValueError):
        return None


def refresh_minifig_prices():
    """Fetch BrickLink last-6-month SOLD averages (Used + New) for every owned
    minifig that has a BrickLink id, and store them back into the local
    collection (`price_used`/`price_new`/`price_updated`). Figs without a
    BrickLink id can't be priced (Rebrickable exposes none) and are skipped.
    Returns a summary. LOCAL-ONLY — the collection is empty on Render."""
    coll = _load_meta(MINIFIG_COLLECTION_PATH)
    targets = [(fn, e["bl_id"]) for fn, e in coll.items() if e.get("bl_id")]
    results, priced, failed = {}, 0, 0

    for fig_num, bl_id in targets:
        guide = _bl_sold_price("MINIFIG", bl_id)
        pu, pn = _bl_avg(guide, "U"), _bl_avg(guide, "N")
        if pu is None and pn is None:
            failed += 1
        else:
            priced += 1
            results[fig_num] = (pu, pn)
        time.sleep(0.4)  # be polite to the BrickLink API

    now = datetime.datetime.now().isoformat(timespec="seconds")
    # Merge into a FRESH read so a concurrent add/remove isn't clobbered.
    with _meta_lock:
        cur = _load_meta(MINIFIG_COLLECTION_PATH)
        for fig_num, (pu, pn) in results.items():
            if fig_num in cur:
                cur[fig_num]["price_used"] = pu
                cur[fig_num]["price_new"] = pn
                cur[fig_num]["price_updated"] = now
        _save_meta(MINIFIG_COLLECTION_PATH, cur)

    summary = {"total": len(coll), "priced": priced, "failed": failed,
               "skipped_no_bl_id": len(coll) - len(targets), "updated": now}
    print(f"[minifig prices] {summary}", file=sys.stderr)
    return summary


_minifig_price_running = {"on": False}


@app.route("/api/minifig_prices/refresh", methods=["POST"])
def minifig_prices_refresh():
    """Manually trigger a BrickLink price refresh for the minifig collection
    (the daily 5am launchd job calls refresh_minifig_prices() directly). Runs in
    a background thread so the request returns immediately."""
    if _minifig_price_running["on"]:
        return jsonify({"status": "already running"}), 202
    _minifig_price_running["on"] = True  # set before returning so /status reflects it

    def _run():
        try:
            refresh_minifig_prices()
        except Exception as e:
            print(f"[minifig prices] error: {e}", file=sys.stderr)
        finally:
            _minifig_price_running["on"] = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"}), 202


@app.route("/api/minifig_prices/status")
def minifig_prices_status():
    """Whether a minifig price refresh is currently running (for the UI poll)."""
    return jsonify({"running": _minifig_price_running["on"]})


# ── Owned sets ("My Sets" collection) — the user's Rebrickable set collection
#    at /users/{token}/sets/. Lets you mark a searched set as owned. ──

@app.route("/api/owned_sets/<set_num>")
def owned_set_status(set_num):
    """Is this set in the user's collection? → {owned, quantity}."""
    resp = http.get(
        f"{RB_BASE}/users/{USER_TOKEN}/sets/{set_num}/",
        params={"key": API_KEY},
    )
    if resp.status_code == 200:
        meta = _load_meta(SET_META_PATH).get(set_num) or {}
        return jsonify({
            "owned": True,
            "quantity": resp.json().get("quantity", 1),
            "condition": meta.get("condition"),
            "price_paid": meta.get("price_paid"),
        })
    if resp.status_code == 404:
        return jsonify({"owned": False, "quantity": 0})
    return jsonify({"owned": False, "quantity": 0, "error": resp.text[:200]}), resp.status_code


@app.route("/api/owned_sets/<set_num>/meta", methods=["POST"])
def set_owned_meta(set_num):
    """Save purchase metadata (condition + price paid) for an owned set.
    Body: {condition: "used"|"new"|null, price_paid: number|null}. Pass both
    null/empty to clear them — but the record is only dropped once it's
    carrying nothing at all, so a cached BrickLink market price
    (price_used/price_new, from refresh_set_prices) survives a condition/
    price-paid edit instead of being wiped by a full overwrite."""
    clean = _clean_meta(request.json or {}) or {"condition": None, "price_paid": None}
    with _meta_lock:
        meta = _load_meta(SET_META_PATH)
        entry = meta.get(set_num) or {}
        entry["condition"] = clean.get("condition")
        entry["price_paid"] = clean.get("price_paid")
        if not any(entry.get(k) is not None for k in
                   ("condition", "price_paid", "price_used", "price_new")):
            meta.pop(set_num, None)
        else:
            meta[set_num] = entry
        _save_meta(SET_META_PATH, meta)
    return jsonify({
        "condition": entry.get("condition"),
        "price_paid": entry.get("price_paid"),
    })


@app.route("/api/add_set", methods=["POST"])
def add_set():
    """Add a set to the user's collection (merges quantity if already owned)."""
    data = request.json
    set_num = data["set_num"]
    quantity = int(data.get("quantity", 1))

    print(f"[add_set] set_num={set_num} qty={quantity}")
    item_url = f"{RB_BASE}/users/{USER_TOKEN}/sets/{set_num}/"
    existing = http.get(item_url, params={"key": API_KEY})

    if existing.status_code == 200:
        current_qty = int(existing.json().get("quantity", 0))
        new_qty = current_qty + quantity
        resp = http.put(item_url, params={"key": API_KEY}, data={"quantity": new_qty})
        result = resp.json() if resp.content else {}
        result.update({"_updated": True, "_previous_quantity": current_qty, "quantity": new_qty})
        return jsonify(result), resp.status_code
    else:
        resp = http.post(
            f"{RB_BASE}/users/{USER_TOKEN}/sets/",
            params={"key": API_KEY},
            data={"set_num": set_num, "quantity": quantity},
        )
        print(f"[add_set] POST {resp.status_code}: {resp.text[:200]}")
        result = resp.json() if resp.content else {}
        if resp.status_code in (200, 201):
            result["quantity"] = result.get("quantity", quantity)
        return jsonify(result), resp.status_code


def _build_subtract_record(key, set_num, list_id, list_name, removed):
    """Fill in a "Subtract a Set" record: split the set's full inventory into
    what was pulled into the list (subtracted) vs. what wasn't needed and is now
    spare (remaining). `removed` is {"part|color": qty} from the confirmed
    preview. Runs in a background thread (the set part-fetch pages through
    throttled GETs); overlays colour-specific catalog images when present."""
    fetched = _fetch_set_parts(set_num)
    rec = {}
    if fetched is None:
        rec.update({"building": False, "error": True})
    else:
        set_name, set_img, set_parts, _total = fetched
        subtracted, remaining = [], []
        sub_pieces = remain_pieces = 0
        for sp in set_parts.values():
            rk = f"{sp['part_num']}|{sp['color_id']}"
            took = int(removed.get(rk, 0))
            took = max(0, min(took, sp["quantity"]))     # never exceed the set's own count
            left = sp["quantity"] - took
            if took > 0:
                subtracted.append({**sp, "quantity": took})
                sub_pieces += took
            if left > 0:
                remaining.append({**sp, "quantity": left})
                remain_pieces += left
        for grp in (subtracted, remaining):
            grp.sort(key=lambda p: (p.get("name") or "").lower())
        conn = local_db()
        if conn is not None:
            try:
                pairs = [(p["part_num"], p["color_id"]) for p in subtracted + remaining
                         if p["color_id"] is not None]
                imgs = _local_part_color_imgs(conn, pairs)
                for p in subtracted + remaining:
                    if p["color_id"] is not None:
                        local_img = imgs.get((p["part_num"], int(p["color_id"])))
                        if local_img:
                            p["img_url"] = local_img
            finally:
                conn.close()
        rec.update({
            "set_name": set_name,
            "set_img": set_img,
            "subtracted": subtracted,
            "remaining": remaining,
            "sub_pieces": sub_pieces,
            "remain_pieces": remain_pieces,
            "building": False,
            "error": False,
        })
    with _meta_lock:
        data = _load_meta(SUBTRACT_RECORDS_PATH)
        data[key] = {**(data.get(key) or {}), **rec}
        _save_meta(SUBTRACT_RECORDS_PATH, data)


@app.route("/api/subtract_records")
def subtract_records_list():
    """Past "Subtract a Set" runs, newest first, each split into subtracted vs.
    remaining (spare) pieces. LOCAL-ONLY (.subtract_records.json is ephemeral on
    Render)."""
    data = _load_meta(SUBTRACT_RECORDS_PATH)
    items = sorted(data.values(), key=lambda r: r.get("ts") or 0, reverse=True)
    return jsonify({"results": items}), 200


@app.route("/api/subtract_records/<key>", methods=["DELETE"])
def subtract_record_delete(key):
    """Dismiss a subtract-a-set card."""
    with _meta_lock:
        data = _load_meta(SUBTRACT_RECORDS_PATH)
        if data.pop(key, None) is not None:
            _save_meta(SUBTRACT_RECORDS_PATH, data)
    return '', 204


@app.route("/api/remove_set_one", methods=["POST"])
def remove_set_one():
    """Decrement an owned set by 1 (delete the entry if it hits 0)."""
    data = request.json
    set_num = data["set_num"]
    item_url = f"{RB_BASE}/users/{USER_TOKEN}/sets/{set_num}/"
    existing = http.get(item_url, params={"key": API_KEY})

    if existing.status_code == 404:
        return jsonify({"error": "Set is not in your collection.", "quantity": 0}), 404
    if existing.status_code != 200:
        return jsonify(existing.json()), existing.status_code

    current_qty = int(existing.json().get("quantity", 0))
    if current_qty <= 1:
        resp = http.delete(item_url, params={"key": API_KEY})
        if resp.status_code == 204:
            with _meta_lock:
                meta = _load_meta(SET_META_PATH)
                if meta.pop(set_num, None) is not None:
                    _save_meta(SET_META_PATH, meta)
            return jsonify({"_deleted": True, "_previous_quantity": current_qty, "quantity": 0}), 200
        return jsonify(resp.json() if resp.content else {}), resp.status_code

    new_qty = current_qty - 1
    resp = http.put(item_url, params={"key": API_KEY}, data={"quantity": new_qty})
    if resp.status_code == 200:
        return jsonify({"_updated": True, "_previous_quantity": current_qty, "quantity": new_qty}), 200
    return jsonify(resp.json() if resp.content else {}), resp.status_code


@app.route("/api/owned_sets")
def owned_sets_list():
    """The user's owned-sets collection (paginated through, newest Rebrickable order)."""
    rows, err = _rb_collect(f"/users/{USER_TOKEN}/sets/", max_pages=100)
    if err and not rows:
        return jsonify({"error": "Couldn't fetch your sets", "results": []}), err
    meta = _load_meta(SET_META_PATH)
    out = []
    for it in rows:
        s = it.get("set") or {}
        m = meta.get(s.get("set_num")) or {}
        out.append({
            "set_num": s.get("set_num"),
            "name": s.get("name"),
            "year": s.get("year"),
            "num_parts": s.get("num_parts"),
            "img_url": s.get("set_img_url"),
            "quantity": it.get("quantity", 1),
            "condition": m.get("condition"),
            "price_paid": m.get("price_paid"),
            "price_used": m.get("price_used"),
            "price_new": m.get("price_new"),
            "price_updated": m.get("price_updated"),
        })
    return jsonify({"results": out, "count": len(out)})


def _owned_set_nums():
    """Page through the user's Rebrickable set collection and return just the
    set_nums — the minimal "what do we own" list refresh_set_prices() needs
    (no per-set image/name fan-out)."""
    rows, _err = _rb_collect(f"/users/{USER_TOKEN}/sets/", max_pages=100)
    return [num for it in rows if (num := (it.get("set") or {}).get("set_num"))]


def refresh_set_prices():
    """Fetch BrickLink last-6-month SOLD averages (Used + New) for every owned
    set and store them back into the local set-meta store
    (`price_used`/`price_new`/`price_updated`, alongside the existing
    condition/price_paid). Mirrors refresh_minifig_prices(). Unlike minifigs,
    every set has a usable BrickLink id (its set_num, +"-1" if bare — see
    get_set_price), so none are skipped. Returns a summary.
    LOCAL-ONLY — `.set_meta.json` is empty/ephemeral on Render."""
    set_nums = _owned_set_nums()
    results, priced, failed = {}, 0, 0

    for set_num in set_nums:
        bl_no = set_num if "-" in set_num else f"{set_num}-1"
        guide = _bl_sold_price("SET", bl_no)
        pu, pn = _bl_avg(guide, "U"), _bl_avg(guide, "N")
        if pu is None and pn is None:
            failed += 1
        else:
            priced += 1
            results[set_num] = (pu, pn)
        time.sleep(0.4)  # be polite to the BrickLink API

    now = datetime.datetime.now().isoformat(timespec="seconds")
    # Merge into a FRESH read so a concurrent meta edit isn't clobbered.
    with _meta_lock:
        cur = _load_meta(SET_META_PATH)
        for set_num, (pu, pn) in results.items():
            entry = cur.get(set_num) or {}
            entry["price_used"] = pu
            entry["price_new"] = pn
            entry["price_updated"] = now
            cur[set_num] = entry
        _save_meta(SET_META_PATH, cur)

    summary = {"total": len(set_nums), "priced": priced, "failed": failed, "updated": now}
    print(f"[set prices] {summary}", file=sys.stderr)
    return summary


_set_price_running = {"on": False}


@app.route("/api/set_prices/refresh", methods=["POST"])
def set_prices_refresh():
    """Manually trigger a BrickLink price refresh for the owned-sets collection
    (the daily launchd job calls refresh_set_prices() directly). Runs in a
    background thread so the request returns immediately."""
    if _set_price_running["on"]:
        return jsonify({"status": "already running"}), 202
    _set_price_running["on"] = True  # set before returning so /status reflects it

    def _run():
        try:
            refresh_set_prices()
        except Exception as e:
            print(f"[set prices] error: {e}", file=sys.stderr)
        finally:
            _set_price_running["on"] = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"}), 202


@app.route("/api/set_prices/status")
def set_prices_status():
    """Whether a set price refresh is currently running (for the UI poll)."""
    return jsonify({"running": _set_price_running["on"]})


def _bl_sold_price(item_type, item_no):
    """BrickLink last-6-months SOLD price guide for an item, both Used and New.
    item_type: MINIFIG | SET | PART. Returns {"U": {...}, "N": {...}} where each
    value is BrickLink's price 'data' (avg_price/min_price/max_price/unit_quantity
    /qty_avg_price). Empty/partial if BrickLink is unavailable (e.g. on cloud)."""
    out = {}
    if not (BL_CONSUMER_KEY and BL_TOKEN):
        return out
    auth = OAuth1(BL_CONSUMER_KEY, BL_CONSUMER_SECRET, BL_TOKEN, BL_TOKEN_SECRET)
    url = f"{BL_BASE}/items/{item_type}/{item_no}/price"
    for cond in ("U", "N"):
        params = {"guide_type": "sold", "new_or_used": cond, "currency_code": "USD"}
        # BrickLink's price-guide endpoint is genuinely slow for items with long
        # sold histories and has intermittent slow spells, so use a generous read
        # timeout and retry once on a *timeout* (not on a 4xx, which is a real
        # answer). (connect, read) seconds.
        for attempt in (1, 2):
            try:
                r = http.get(url, params=params, auth=auth, timeout=(5, 15))
                if r.status_code == 200:
                    out[cond] = r.json().get("data", {})
                else:
                    print(f"[BL price] {item_type} {item_no} {cond} → {r.status_code}", file=sys.stderr)
                break  # got a response (even a non-200) — don't retry
            except requests.exceptions.Timeout as e:
                if attempt == 1:
                    print(f"[BL price] {item_type} {item_no} {cond} timeout — retrying", file=sys.stderr)
                    continue
                print(f"[BL price] error: {e}", file=sys.stderr)
            except Exception as e:
                print(f"[BL price] error: {e}", file=sys.stderr)
                break  # non-timeout failure (e.g. connection/DNS) — don't retry
    return out


@app.route("/api/minifig_price/<fig_id>")
def get_minifig_price(fig_id):
    # Keep in sync with _MINIFIG_THEME_MAP in templates/index.html (used there to
    # group/search My Minifigs by theme).
    theme_map = {
        'sw': 'Star Wars', 'hp': 'Harry Potter', 'colhp': 'Harry Potter',
        'lor': 'Lord of the Rings', 'hob': 'The Hobbit', 'loz': 'Legend of Zelda',
        'njo': 'Ninjago', 'nex': 'Nexo Knights', 'cas': 'Castle', 'cty': 'City',
        'twn': 'Town', 'sh': 'Super Heroes', 'dim': 'Dimensions', 'idea': 'Ideas',
        'hs': 'Hidden Side', 'mof': 'Monster Fighters', 'tlm': 'The LEGO Movie',
        'coltlm': 'The LEGO Movie', 'coltlbm': 'The LEGO Batman Movie',
        'col': 'Collectible Minifigures', 'coldis': 'Disney', 'colsim': 'The Simpsons',
        'colmar': 'Marvel Studios', 'coldc': 'DC Super Heroes', 'jw': 'Jurassic World',
        'elf': 'Elves', 'frnd': 'Friends', 'mk': 'Monkie Kid', 'pi': 'Pirates',
        'pm': 'Pirates of the Caribbean', 'adv': 'Adventurers', 'toy': 'Toy Story',
        'sp': 'Space', 'trn': 'Train', 'ovr': 'Overwatch', 'vik': 'Vikings',
        'ww': 'Western', 'aqu': 'Aquazone', 'gen': 'General',
    }
    prefix = (re.match(r'^([a-z]+)', fig_id.lower()) or re.match(r'', '')).group(0)
    results = {"category": theme_map.get(prefix, 'Minifigure')}
    results.update(_bl_sold_price("MINIFIG", fig_id))
    return jsonify(results)


@app.route("/api/set_price/<set_num>")
def get_set_price(set_num):
    """BrickLink last-6-months sold price (Used + New) for a set. BrickLink set
    ids carry the variant suffix (e.g. 75300-1), matching Rebrickable's set_num;
    a bare number defaults to '-1'."""
    bl_no = set_num if "-" in set_num else f"{set_num}-1"
    return jsonify(_bl_sold_price("SET", bl_no))


@app.route("/api/minifig_parts/<minifig_id>")
def get_minifig_parts(minifig_id):
    """Fetch parts that make up a minifigure (offline catalog first, API fallback)."""
    local = _local_minifig_parts(minifig_id)
    if local:
        return jsonify({"count": len(local), "results": local})
    try:
        parts_resp = http.get(
            f"{RB_BASE}/lego/minifigs/{minifig_id}/parts/",
            params={"key": API_KEY},
            timeout=8,
        )
        if parts_resp.status_code == 200:
            return jsonify(parts_resp.json())
        else:
            return jsonify({"error": "Unable to fetch minifigure parts", "count": 0, "results": []}), 404
    except Exception as e:
        print(f"Error fetching minifig parts: {e}")
        return jsonify({"error": str(e), "count": 0, "results": []}), 500


def _api_search_fallback(kind, query, limit):
    """Search the live Rebrickable API when the offline DB is unavailable.

    Returns results in the same shape as the offline search so the frontend
    renders them identically (just with source='api'). Costs API quota.
    """
    if kind == "minifigs":
        resp = rebrickable_get("/lego/minifigs/", params={
            "key": API_KEY, "search": query, "page_size": limit,
        })
        rows = (resp.json().get("results", []) if resp and resp.status_code == 200 else [])
        return [{
            "type": "minifig",
            "fig_num": r.get("set_num"),       # Rebrickable uses set_num for fig id
            "name": r.get("set_name"),
            "num_parts": r.get("num_parts"),
            "img_url": r.get("set_img_url"),
        } for r in rows]

    if kind == "sets":
        resp = rebrickable_get("/lego/sets/", params={
            "key": API_KEY, "search": query, "page_size": limit, "ordering": "-year",
        })
        rows = (resp.json().get("results", []) if resp and resp.status_code == 200 else [])
        return [{
            "type": "set",
            "set_num": r.get("set_num"),
            "name": r.get("name"),
            "year": r.get("year"),
            "part_count": r.get("num_parts"),
            "theme": None,
            "img_url": r.get("set_img_url"),
        } for r in rows]

    # parts
    resp = rebrickable_get("/lego/parts/", params={
        "key": API_KEY, "search": query, "page_size": limit,
    })
    rows = (resp.json().get("results", []) if resp and resp.status_code == 200 else [])
    return [{
        "type": "part",
        "part_num": r.get("part_num"),
        "name": r.get("name"),
        "img_url": r.get("part_img_url"),
        "category": None,
    } for r in rows]


@app.route("/api/resolve_part/<part_id>")
def resolve_part(part_id):
    """Resolve a BrickLink (or Rebrickable) part id to a Rebrickable part via the
    local catalog: exact match → bl_aliases (authoritative BrickLink map) → mold
    heuristic. Used by the voice-add flow, where users speak BrickLink numbers
    (e.g. "3068" → 3068b). Returns {part_num, name, img_url} or 404."""
    p = _local_resolve_part(part_id)
    if p:
        return jsonify(p)
    return jsonify({"error": "not found"}), 404


@app.route("/api/local/search")
def local_search():
    """Catalog search. Prefers the offline SQLite DB (no Rebrickable quota);
    falls back to the live Rebrickable API if the DB is absent.

    Query params:
      q     — search term (matches name or catalog number)
      type  — 'parts' | 'minifigs' | 'sets'  (default 'parts')
      limit — max results (default 30, capped 100)

    Response includes "source": "offline" | "api" so the UI can show which
    data source served the results.
    """
    query = request.args.get("q", "").strip()
    kind = request.args.get("type", "parts").strip().lower()
    try:
        limit = min(int(request.args.get("limit", 30)), 100)
    except (TypeError, ValueError):
        limit = 30

    if not query:
        return jsonify({"error": "Please enter a search term", "results": []}), 400

    conn = local_db()
    if conn is None:
        # No offline catalog → fall back to the live Rebrickable API.
        try:
            results = _api_search_fallback(kind, query, limit)
            return jsonify({"results": results, "count": len(results), "source": "api"})
        except Exception as e:
            print(f"Error in API search fallback: {e}")
            return jsonify({"error": str(e), "results": [], "source": "api"}), 500

    like = f"%{query}%"
    prefix = f"{query}%"
    bl_match = None  # set when a BrickLink minifig id was translated to a name
    try:
        if kind == "minifigs":
            rows = conn.execute(
                """
                SELECT fig_num, name, num_parts, img_url FROM minifigs
                WHERE fig_num = :q OR fig_num LIKE :prefix OR name LIKE :like
                ORDER BY
                  CASE WHEN fig_num = :q THEN 0
                       WHEN fig_num LIKE :prefix THEN 1
                       WHEN name LIKE :prefix THEN 2
                       ELSE 3 END,
                  name
                LIMIT :limit
                """,
                {"q": query, "prefix": prefix, "like": like, "limit": limit},
            ).fetchall()
            results = [{
                "type": "minifig",
                "fig_num": r["fig_num"],
                "name": r["name"],
                "num_parts": r["num_parts"],
                "img_url": r["img_url"],
            } for r in rows]

            # BrickLink minifig id (e.g. sw0131) with no local hit: Rebrickable has
            # no BrickLink minifig ids, so translate the id → name via BrickLink and
            # surface the best-matching Rebrickable figs as candidates to choose from.
            if not results and re.match(r'^[a-z]{1,4}\d{2,5}[a-z]?$', query.lower()):
                bl_info = _bricklink_minifig_lookup(query)
                if bl_info:
                    bl_match = {"id": query, "name": bl_info["name"], "img_url": bl_info.get("img_url")}
                    rows2 = _local_minifig_search_by_name(bl_info["name"], limit)
                    results = [{
                        "type": "minifig",
                        "fig_num": r["fig_num"],
                        "name": r["name"],
                        "num_parts": r["num_parts"],
                        "img_url": r["img_url"],
                    } for r in rows2]

        elif kind == "sets":
            rows = conn.execute(
                """
                SELECT s.set_num, s.name, s.year, s.num_parts, s.img_url, t.name AS theme
                FROM sets s LEFT JOIN themes t ON t.id = s.theme_id
                WHERE s.set_num = :q OR s.set_num LIKE :prefix OR s.name LIKE :like
                ORDER BY
                  CASE WHEN s.set_num = :q THEN 0
                       WHEN s.set_num LIKE :prefix THEN 1
                       WHEN s.name LIKE :prefix THEN 2
                       ELSE 3 END,
                  s.year DESC
                LIMIT :limit
                """,
                {"q": query, "prefix": prefix, "like": like, "limit": limit},
            ).fetchall()
            results = [{
                "type": "set",
                "set_num": r["set_num"],
                "name": r["name"],
                "year": r["year"],
                "part_count": r["num_parts"],
                "theme": r["theme"],
                "img_url": r["img_url"],
            } for r in rows]

        else:  # parts
            rows = conn.execute(
                """
                SELECT p.part_num, p.name, p.img_url, c.name AS category
                FROM parts p LEFT JOIN part_categories c ON c.id = p.part_cat_id
                WHERE p.part_num = :q OR p.part_num LIKE :prefix OR p.name LIKE :like
                ORDER BY
                  CASE WHEN p.part_num = :q THEN 0
                       WHEN p.part_num LIKE :prefix THEN 1
                       WHEN p.name LIKE :prefix THEN 2
                       ELSE 3 END,
                  p.name
                LIMIT :limit
                """,
                {"q": query, "prefix": prefix, "like": like, "limit": limit},
            ).fetchall()
            results = [{
                "type": "part",
                "part_num": r["part_num"],
                "name": r["name"],
                "img_url": r["img_url"],
                "category": r["category"],
            } for r in rows]

        resp = {"results": results, "count": len(results), "source": "offline"}
        if bl_match:
            resp["bl_match"] = bl_match
        return jsonify(resp)
    except Exception as e:
        print(f"Error in local_search: {e}")
        return jsonify({"error": str(e), "results": []}), 500
    finally:
        conn.close()


@app.route("/api/sets/<set_num>/parts")
def get_set_parts(set_num):
    """Fetch all parts in a specific LEGO set from Rebrickable API."""
    try:
        all_parts, err = _rb_collect(f"/lego/sets/{set_num}/parts/")
        if err:
            return jsonify({"error": f"Failed to fetch parts: {err}", "results": []}), err

        formatted = [{
            "part_num": part.get("part", {}).get("part_num"),
            "part_name": part.get("part", {}).get("name"),
            "part_img_url": part.get("part", {}).get("part_img_url"),
            "color_id": part.get("color", {}).get("id"),
            "color_name": part.get("color", {}).get("name"),
            "color_rgb": part.get("color", {}).get("rgb"),
            "quantity": part.get("quantity", 0),
            "is_spare": part.get("is_spare", False)
        } for part in all_parts]

        return jsonify({"results": formatted, "count": len(formatted)})
    except Exception as e:
        print(f"Error fetching set parts: {e}")
        return jsonify({"error": str(e), "results": []}), 500


@app.route("/api/sets/<set_num>/minifigs")
def get_set_minifigs(set_num):
    """Fetch all minifigs in a specific LEGO set from Rebrickable API."""
    try:
        all_figs, err = _rb_collect(f"/lego/sets/{set_num}/minifigs/")
        if err:
            return jsonify({"error": f"Failed to fetch minifigs: {err}", "results": []}), err

        # Rebrickable uses set_num/set_name/set_img_url for minifig fields
        formatted = [{
            "fig_num": fig.get("set_num"),
            "fig_name": fig.get("set_name"),
            "fig_img_url": fig.get("set_img_url"),
            "quantity": fig.get("quantity", 0)
        } for fig in all_figs]

        return jsonify({"results": formatted, "count": len(formatted)})
    except Exception as e:
        print(f"Error fetching set minifigs: {e}")
        return jsonify({"error": str(e), "results": []}), 500


@app.route("/api/import-csv", methods=["POST"])
def import_csv():
    """Import parts from CSV file: part_num, color, quantity"""
    try:
        import csv
        import difflib

        list_id = request.form.get("list_id")
        if not list_id:
            return jsonify({"error": "list_id is required"}), 400

        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "No file selected"}), 400

        # Read and parse CSV
        stream = io.StringIO(file.stream.read().decode("UTF-8"), newline=None)
        csv_data = csv.DictReader(stream)

        # Build color lookup maps (shared cached color list)
        colors = _all_colors()
        color_name_to_id = {c["name"].lower(): c["id"] for c in colors}
        color_names_list = [c["name"] for c in colors]

        # Helper function for fuzzy color matching
        def resolve_color(color_input):
            """Resolve color name with fuzzy matching"""
            if not color_input:
                return None

            color_lower = color_input.lower()

            # 1. Try exact match (case-insensitive)
            if color_lower in color_name_to_id:
                return color_name_to_id[color_lower]

            # 2. Try variations: add/remove hyphens for Trans colors
            if "trans" in color_lower:
                # Try replacing spaces with hyphens
                variant = color_lower.replace(" ", "-")
                if variant in color_name_to_id:
                    return color_name_to_id[variant]
                # Try replacing hyphens with spaces
                variant = color_lower.replace("-", " ")
                if variant in color_name_to_id:
                    return color_name_to_id[variant]

            # 3. Try closest string match (difflib)
            matches = difflib.get_close_matches(color_input, color_names_list, n=1, cutoff=0.75)
            if matches:
                matched_color = matches[0]
                return color_name_to_id.get(matched_color.lower())

            return None

        results = {
            "imported": 0,
            "failed": 0,
            "errors": []
        }

        for row in csv_data:
            try:
                # Normalize column names to lowercase for case-insensitive matching
                row_lower = {k.lower(): v for k, v in row.items()}

                part_num = row_lower.get("part_num", "").strip()
                color_name = row_lower.get("color", "").strip()
                quantity = int(row_lower.get("quantity", 1))

                if not part_num:
                    results["failed"] += 1
                    results["errors"].append("Missing part_num in row")
                    continue

                # Resolve color name to ID (with fuzzy matching)
                color_id = resolve_color(color_name)
                if not color_id:
                    results["failed"] += 1
                    results["errors"].append(f"Unknown color '{color_name}' for part {part_num}")
                    continue

                # Add part using existing add_part logic
                existing = http.get(
                    f"{RB_BASE}/users/{USER_TOKEN}/partlists/{list_id}/parts/{part_num}/{color_id}/",
                    params={"key": API_KEY},
                )

                if existing.status_code == 200:
                    # Update existing
                    current_qty = existing.json().get("quantity", 0)
                    new_qty = current_qty + quantity
                    http.put(
                        f"{RB_BASE}/users/{USER_TOKEN}/partlists/{list_id}/parts/{part_num}/{color_id}/",
                        params={"key": API_KEY},
                        data={"quantity": new_qty},
                    )
                else:
                    # Create new
                    http.post(
                        f"{RB_BASE}/users/{USER_TOKEN}/partlists/{list_id}/parts/",
                        params={"key": API_KEY},
                        data={"part_num": part_num, "color_id": color_id, "quantity": quantity},
                    )

                results["imported"] += 1

            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"Error importing row: {str(e)}")

        return jsonify(results)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    import socket
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except socket.gaierror:
        local_ip = "127.0.0.1"
    port = int(os.environ.get("PORT", 5001))
    print(f"\n  Open on your phone: http://{local_ip}:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
