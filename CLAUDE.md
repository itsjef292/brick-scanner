# CLAUDE.md

Guidance for Claude Code when working in this repo.

> **Keep this file lean.** It reloads into context *every* turn, so size costs
> tokens/speed (hook warns at 32k, harness at 40k). It should hold only durable
> working guidance: architecture, commands, rules, conventions, gotchas. **Do not**
> add changelog/"how I built X" narratives — those go in **`CHANGELOG.md`**.
> Machine setup / Tailscale / Render-keys detail go in **`SETUP.md`**. Edit stale
> lines in place; don't append new sections. Prefer `git log` over re-expanding this.

## Project Overview

**Brick Scanner** identifies LEGO parts/minifigs from phone photos: capture →
Brickognize detection → BrickLink↔Rebrickable id mapping → server-side LAB color
detection → add to Rebrickable inventory / local collections, with BrickLink price
lookups. **Stack:** Flask (Python 3) + vanilla JS (no frameworks). All CSS/JS lives
inline in `templates/index.html`.

---

## Commands

```bash
python3 app.py                   # dev server on :5001 (Flask debug auto-reload)
./start.sh                       # foreground run; prints the private Tailscale URL
                                 #   (stop the autostart agent first — both bind :5001)
pip3 install flask requests python-dotenv requests-oauthlib   # deps
cp .env.example .env             # then fill in credentials (below)

# Offline catalog (local search; LOCAL-ONLY — see note):
python3 build_brick_db.py        # build ./brick_parts.db (~195 MB) from "Brick Parts/" CSVs
python3 download_csvs.py         # download the Rebrickable CSV dump → "Brick Parts/"
python3 refresh_catalog.py [--force]   # daily HEAD-check, rebuild + atomic swap if changed
```

**`.env` (git-ignored, also set in Render dashboard with `sync: false`):**
`REBRICKABLE_API_KEY`, `REBRICKABLE_USER_TOKEN`, and BrickLink OAuth1
`BL_CONSUMER_KEY`/`BL_CONSUMER_SECRET`/`BL_TOKEN`/`BL_TOKEN_SECRET`.

New-machine setup (secrets, catalog rebuild, launchd agents via
`./install_agents.sh`): see **`SETUP.md`**.

---

## LOCAL vs RENDER (important)

Render runs on an **ephemeral filesystem** with **no BrickLink creds**, so a whole
class of features is **LOCAL-ONLY** and silently degrades in production:

- **Offline catalog + refresh + change-tracking + daily job** — Render rebuilds
  `brick_parts.db` from scratch each deploy with no prior catalog to diff. Gated by
  `IS_RENDER` (the `RENDER` env var): `can_refresh` → false, scan-screen footer
  hidden, `POST /api/catalog/refresh` → 403. The app degrades gracefully when
  `brick_parts.db` is absent (offline search returns "not available").
- **Local collections** (`.minifig_collection.json`, `.set_meta.json`) and all
  **BrickLink pricing** — empty/unavailable on Render.

`Brick Parts/`, `brick_parts.db`, and all `.json`/`.log` state are git-ignored.

---

## Architecture

### Backend (`app.py`) — Flask, OAuth1 signing for BrickLink

**RULE:** every Rebrickable call **must** go through `rebrickable_get()` (60 req/min
throttle). Never call `requests.get` on Rebrickable directly. Throttling is
automatic per request, including in pagination loops (`while url: … url = resp.json()["next"]`).

Endpoints (grep `@app.route` for the full list; notable ones):

- **Parts lists:** `GET/POST /api/partlists`, `DELETE /api/partlists/<id>`,
  `GET …/<id>/parts` (paginated), `GET …/<id>/parts_all` (full flat dump for live
  search; color imgs from local `part_colors` via `_local_part_color_imgs`),
  `GET …/<id>/parts/<part>/<color>` (in-list check), `GET /api/part_in_lists/…`
  (all lists holding a part), `POST /api/add_part`, `POST /api/remove_part_one`,
  `GET …/<id>/bricklink_wanted` (Wanted-List XML via `bl_aliases`/`bl_colors`).
- **Minifig info:** `GET /api/minifig_sets/<set_num>`,
  `GET /api/minifig_variants/<bl_id>` (variants sharing the id's numeric base, e.g.
  sw0574→sw0574a; probes BrickLink — no variant API — and caches
  `.bl_minifig_variants.json`, 30-day TTL),
  `GET /api/minifig_price/<fig_id>` & `GET /api/set_price/<set_num>` (BrickLink
  6-mo SOLD, Used+New, via `_bl_sold_price`).
- **Owned Sets ("My Sets")** — the user's Rebrickable set collection at
  `/users/{token}/sets/`. `GET /api/owned_sets`, `GET/POST` for one set,
  `POST /api/add_set`, `POST /api/remove_set_one`,
  `POST …/<set_num>/meta` (condition + price_paid in `.set_meta.json`; preserves a
  cached market price). Prices: `refresh_set_prices()` (`_setMarketPrice` chip,
  spent + market totals on the count line).
- **Owned Minifigs ("My Minifigs") — fully LOCAL.** Rebrickable's minifig
  collection is read-only, so this lives in `.minifig_collection.json` keyed by
  fig_num (`{quantity, condition, price_paid, name, img_url, fig_num, bl_id}`).
  BrickLink **variants** (`sw0574a`) are separate entries via `_minifig_ckey`
  (suffixed → `fig_num#<suffix>`; base unchanged → backward compatible); owned
  routes take optional `bl_id` (body) / `?bl=` (query) to address one. Routes:
  `GET /api/owned_minifigs[/<fig_num>]`, `POST /api/add_minifig`,
  `POST /api/remove_minifig_one`, `…/<fig>/meta`, `…/<fig>/blid` (manual BrickLink
  id entry → clears prices so refresh re-fetches). Prices: `refresh_minifig_prices()`
  for figs with a `bl_id`. Shared JSON helpers `_load_meta`/`_save_meta`/`_clean_meta`.
- **Offline search:** `GET /api/local/search?q=&type=parts|minifigs|sets` — prefers
  `brick_parts.db`, **falls back to live Rebrickable when absent** (production);
  response carries `"source": "offline"|"api"`. BrickLink minifig ids (e.g. sw0131)
  with no local hit → translated via BrickLink (`_bricklink_minifig_name`) and
  returned as ranked candidates + `"bl_match"`.
- **Core:** `POST /api/identify` (Brickognize), `GET /api/colors` (cached),
  `GET /api/part/<n>`, `GET /api/part_colors/<n>`.

Price refreshes are threaded (`POST …/refresh` + `…/status` poll) and run daily via
launchd (minifigs 05:00, sets 05:30, catalog local). LOCAL-ONLY.

### Frontend (`templates/index.html`) — 5 screens + 2 modes

Screens: **Scan → Loading → Identify → Lists → Success**. Modes: **Parts** vs
**Minifigs** (changes list endpoints + copy). Pure vanilla JS; no frameworks.

Notable subsystems (names given for grep — see code/CHANGELOG for detail):

- **Live auto-scan** — viewfinder captures every ~1.5s → `/api/identify`
  (`startLiveScan`/`liveTick`/`syncLiveScan`). Secure-context only (HTTPS/localhost),
  else falls back to "Take Photo". Auto-starts only if camera permission is already
  `granted` (`_queryCamPerm`) — never an unsolicited prompt.
- **Color detection/matching** — LAB sampling from bbox (center-40% fallback).
  `findClosestLegoColor(..., preferredIds, trustShortlist)`: **exactly one predicted
  color → trust it outright** (pixel sampling can mislead); **multiple/none → match
  the sampled pixel against the part's full palette**, with predicted colors as a
  −15 *prior* (not a hard filter — Brickognize sometimes omits the true color).
  Trans-/Glow/Satin penalized unless detected.
- **EXIF rotation** — iPhone portrait (orientation 6) rotated to landscape raw coords for bbox alignment.
- **Gestures** — swipe-left "Remove 1" (`makeSwipeRemovable`/`consumeSwipe`),
  swipe-from-left-edge back (`backSwipe`/`BACK_TARGETS`), pull-to-refresh
  (`pullToRefresh`/`_ptrRefreshFn`). All `_overlayOpen()`-guarded.
- **Resume freshness** — `refreshActiveScreenData()` on visibility/pageshow;
  `checkForUpdate()` gated by `_safeToReload()` self-updates the PWA without
  interrupting a scan (content-hash `app-version` vs `/api/version`).
- **Live search** — Lists/My-Minifigs filter in memory (`_listAllParts`,
  `renderMyMinifigsGrid`); steppers keep the in-memory list + count in sync.
- **Lazy images** — `lazyLoadImages()` (IntersectionObserver); iOS Safari's native
  `loading="lazy"` is unreliable and eager loading floods the connection pool.
- **Inventory status** — on color select, `checkInventoryStatus()` (race-guarded by
  `inventoryCheckToken`) + `fetchPartInLists()` ("Found in" cross-list +/−).
- **PWA** — `manifest.webmanifest` + root-scoped `/sw.js`. SW: navigations
  network-first, `/static/` stale-while-revalidate, **`/api/` + cross-origin never
  cached**. Secure-context only.
- **Loading screen** — `animateScan()` beam + simulated `#loadingPct` (identify is
  one opaque request).

---

## UI / design system

Full direction + patterns in **`.interface-design/system.md`** — **read it before
any UI work.** Sorting-station direction: **Inter** (UI) + **Space Mono** (catalog
data only: part #s, ids, quantities, prices, dates). Single **azure** accent
(historically named `--yellow`), bluish-gray elevation. **Always use the CSS custom
properties** (in CSS *and* JS-generated inline styles), never hardcoded hex:

```
--yellow #3B9EFF (accent)  --bg #0C1014  --surface #141A22  --surface2 #1B2330
--surface3 #232E3D (inputs/2ndary btns)  --socket #080B0F (inset)
--border rgba(150,180,215,.10)  --border-bright rgba(150,180,215,.20)
--text #EAEEF4  --muted #9EAAB9  --muted2 #697686
--green #46C97E/--green-dim (in-inventory)  --red #F0564B/--red-bg/--red-text (remove)
--font-display/--font-body 'Inter'  --font-mono 'Space Mono'
```

**Avoid** (see system.md): emoji chrome icons, magnifying-glass scan metaphor, flat
rectangular swatches, the global dot-grid texture. **No `title=` tooltips** — the app
runs on iPhone where long-press shows the copy menu, not the tooltip; surface info
visibly instead.

---

## Conventions & gotchas

- **Adding a part-list feature:** add the endpoint to `app.py` (use Rebrickable's
  `/users/{token}/partlists/…` as reference, via `rebrickable_get()`); colors are
  cached at `GET /api/colors`; wire UI/JS in index.html; **test on iOS Safari**.
- **Debugging color:** `#debugSwatch`/`#debugLabel` visualize the sample; raw
  Brickognize response is dumped to `/tmp/brk_full.json` each identify.
- **iOS Safari quirks:** hard-refresh with Cmd+Shift+R; number↔text input
  conversions misbehave (reset type early); **`overflow-x:hidden` on `.screen`
  clips `position:absolute` children** → render popups/dropdowns as siblings outside
  the clipped ancestor.
- CORS: all Brickognize/Rebrickable/BrickLink calls go through Flask, never the browser.

---

## Deployment

**Two instances:** local `http://127.0.0.1:5001` (also private via Tailscale — see
SETUP.md) and production `https://brick-scanner.onrender.com` (auto-redeploys ~1–2
min after a push to `main`).

**Golden Rule:** all dev is local; **push to `main` only when explicitly told**
("Push to main" / "Deploy this") → `git add . && git commit -m "…" && git push origin main`.
Render/Tailscale/keys/cost detail: **`SETUP.md`**.

---

## Key files

- **app.py** — Flask server, all endpoints, BrickLink OAuth1.
- **templates/index.html** — 5500+ lines: HTML + CSS + vanilla JS + canvas color detection.
- **.interface-design/system.md** — design system; read before UI work.
- **build_brick_db.py** — builds `brick_parts.db` from the `Brick Parts/` CSV dump.
- **static/** — minifig PNG, header SVG, PWA assets (`manifest.webmanifest`, `sw.js`, icons).
- **native/ + BUILD_IOS.md** — Capacitor iOS shell; loads the live web app over
  Tailscale and adds on-device Data Matrix scanning for the CMF tab (feature-detected
  via `_cmfNative()`; web flow untouched). `ios/` + `node_modules/` git-ignored.
- **SETUP.md** — new-machine setup, Tailscale, Render keys/cost.
- **CHANGELOG.md** — full history of notable changes.
- **install_agents.sh** + `com.brickscanner.*.plist` / `refresh_*.sh|py` — launchd
  agents (autostart server; daily catalog + minifig/set price refreshes). LOCAL-ONLY.
