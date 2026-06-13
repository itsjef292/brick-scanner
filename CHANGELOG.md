# Changelog

History of notable changes to Brick Scanner. Newest first. (Moved out of
`CLAUDE.md` to keep that file lean — see git history for full diffs.)

**Dockerfile restored — Render builds it (June 2026):**
- The cleanup below wrongly classed `Dockerfile`/`.dockerignore` as stale
  GCP leftovers: the Render service is a Docker-runtime service, so deleting
  them broke production builds ("Exited with status 1"). Restored both;
  `render.yaml`'s `runtime: python` line is unused (a Render service's
  runtime can't change after creation). `SETUP.md` + `CLAUDE.md` now say so.

**Repo cleanup: stale deployment paths + unused assets (June 2026):**
- Removed stale non-Render deployment artifacts: `DEPLOY.md` (old Google Cloud
  Run guide), `Dockerfile`, `.dockerignore`, `INSTALL-linux.md`, and `deploy/`
  (Linux systemd units) — production is Render via `render.yaml`'s native
  Python runtime. Updated `SETUP.md`'s deployment-files list accordingly.
  (`Dockerfile`/`.dockerignore` restored same day — see entry above.)
- Removed stray root screenshots `parted-out.png`/`subtracted-sets.png` and
  unused `static/Alignment_Fig.jpg`, `static/parts_icon.jpg`,
  `static/parts_icon.png` — all unreferenced in code or docs.
- Slimmed `AGENTS.md` from a stale, duplicated translation of the architecture
  doc (still called the app "LEGO Scanner", referenced a removed ngrok helper)
  to a short pointer at `CLAUDE.md` as the single source of truth.

**UI consistency pass against the design system (June 2026):**
- New shared `.section-caret` (azure SVG, rotated by `[aria-expanded]` CSS)
  replaces the last two `▶`/`▼` text glyphs swapped in JS (catalog-changes
  toggle, minifig Parts section).
- All `title=` tooltips → `aria-label` (iOS long-press never showed them).
- Raw hex → tokens in JS-generated styles: error text `#c00`→`--red`, CSV
  warning→`--red-text`, quick-remove/shopping red buttons→`--red-bg/-text/-border`,
  comparison-row greens/reds→`--green-bg/-border` + `--red-border`, placeholder
  thumb `#f0f0f0`→`--surface3`; voice-mic pulse `#EF4444`→`--red`, error text
  →`--red-text`. Inline green stud dots unified on `--stud-sheen` over
  `--green` (were two different hand-rolled gradients).
- Flat square colour swatches in Shopping/comparison views are now glossy
  round studs (`--stud-sheen`, 50% radius); dropped the `🔄` emoji from the
  refresh button. Verified both themes at iPhone size, zero console errors.

**Performance + redundancy pass on `app.py` (June 2026):**
- All 35 outbound API calls now share one pooled `requests.Session` (`http`,
  keep-alive instead of a TLS handshake per call) with a default 10s timeout —
  22 calls previously had none and could hang a worker thread indefinitely.
- `GET /api/partlists/<id>/parts` no longer fans out up to 50 per-row
  Rebrickable image lookups per page; color-accurate images come from one
  batched `_local_part_color_imgs` query (per-item API lookup kept as fallback).
- `_local_color_id_by_name` (called per candidate color on every identify)
  caches the ~275-row colors table in memory instead of opening a SQLite
  connection per call; `/bins/print` batches part-name lookups into IN queries.
- Eight copy-pasted Rebrickable pagination loops → one `_rb_collect()` helper;
  `/api/sets/<n>/parts|minifigs` now respect the rate-limit throttle.
- Removed dead code: `/api/verify-part`, `/api/search_sets`, `/api/status`,
  `check_rate_limited`, `RATE_LIMIT_STATUS`. Collapsed the three copies of the
  color-list fetch (`/api/colors`, `/api/colors-hybrid`, CSV import) into one
  cached `_all_colors()`; `/api/colors-hybrid` is now just an alias route and
  the frontend fetches `/api/colors` directly. `get_minifig` reuses
  `_bricklink_minifig_lookup`; both price refreshers share `_bl_avg`.
  Net −306 lines.

**Subtracted Sets — what a set gave the list vs. what's left over (June 2026):**
- Confirming a **Subtract a Set** run now records, per set+list, which of the
  set's pieces were pulled into the list (**subtracted**) vs. which weren't
  needed and are now **spare/remaining** (set qty − subtracted). Stored in
  `.subtract_records.json` keyed `{set_num}__{list_id}` (LOCAL-ONLY; git-ignored).
- `subtract_set` seeds a placeholder then spawns `_build_subtract_record` in a
  background thread (re-fetches the set's full inventory via shared
  `_fetch_set_parts` — also used by `set_overlap` — and splits it by the
  confirmed `removed` map), so the confirm never blocks; the card shows
  `building…` until ready.
- New **"Subtracted Sets"** section at the top of Part Lists → My Lists
  (`loadSubtractRecords`/`renderSubtractRecords`): one collapsible card per run
  (set # + name + "N subtracted · M left · from <list>") expanding to two
  groups — *Subtracted into list* (green) and *Remaining (spare)* — each a part
  list (image, colour, part #, ×qty). `×` dismiss → `DELETE /api/subtract_records/
  <key>`; `GET /api/subtract_records` serves newest-first; re-polls every 2s
  while building. A clean confirm jumps to My Lists so the card is visible.

**Bin QR stickers — scan a bin, take a piece out (June 2026):**
- **Printable sticker sheet** at `GET /bins/print` (standalone light-themed
  `templates/bin_stickers.html`, linked from Manage list → "Print Bin QR
  Stickers"): one dashed-outline card per distinct bin (QR + big mono label +
  part numbers/names from the local catalog). The QR encodes
  `<base>/?bin=<label>`; the base URL is an editable field (persisted in
  `localStorage('binStickerBase')`) since stickers must point at the host the
  *phone* uses (Tailscale), not where they're printed. QRs drawn client-side by
  vendored `static/qrcode.min.js` (kazuhikoarase qrcode-generator, MIT).
- **Two scan paths to `screen-bin`:** the iPhone camera app opens the deep link
  `/?bin=<label>` (handled at boot, then `history.replaceState` so reload won't
  re-open), and the live viewfinder decodes QRs mid-scan via vendored
  `static/jsQR.js` (MIT, lazy-loaded on live-scan start; `_binFromCanvas` checks
  each tick + shutter frame before the Brickognize upload and jumps straight to
  the bin).
- **Bin screen** (`openBinScreen`/`renderBinRows`/`binAdjust`): aggregates
  every part filed under the label across ALL parts lists (parts_all per list,
  filtered client-side), one row per part+color+list with one-tap red **"− 1"**
  / green **"+ 1"** pills (`/api/remove_part_one` / `/api/add_part`) flanking
  the quantity + toast with the resulting count.
  Deliberately **no auto-remove on scan** — a reload or double-scan must never
  silently double-decrement. Back-swipe → Scan tab.

**Sorting-bin locations on parts lists (June 2026):**
- Each row on the Part Lists screen gets a tappable mono **bin chip** after the
  part number (`.bin-chip`; dashed "+ Bin" when unset) recording where the part
  physically lives in the sorting bins. Tap → inline input swap (16px font so
  iOS Safari doesn't zoom), Enter/blur saves, Esc cancels, empty clears.
- Stored in **`.part_bins.json`** keyed by **part number only** (a mould lives
  in one bin regardless of colour or which list references it), so the same bin
  shows on every colour-row of the part across all lists. `GET /api/part_bins`
  (full map, joined client-side at list load) + `POST /api/part_bins/<part_num>`.
  Same `_load_meta`/`_save_meta` machinery as set meta; effectively LOCAL-ONLY
  (ephemeral on Render). The list live-search also matches bin labels, so typing
  "A3" filters to that bin's contents.

**Retiring-sets section (June 2026):**
- New **"Retiring Soon"** button on the Sets toolbar → `screen-retirement`:
  LEGO retirement dates from the **Brick Tap community sheet** (data sourced
  from Brick Hound), searchable by name/number and filterable by **theme** and
  **retirement month** (month dropdown includes a "Recently retired" group,
  newest first). Rows grouped under month labels; thumbs guessed from
  `cdn.rebrickable.com/media/sets/{num}-1.jpg`; tap opens the LEGO.com product
  page (affiliate redirects unwrapped to plain `lego.com` URLs at parse time).
- **Data pipeline:** the sheet is link-public, so `refresh_retirement.py`
  downloads the xlsx export (no Google auth), parses the "Sorted by Retirement
  Date" + "Recently Retired" tabs (header row sniffed; rows without a real
  datetime in the date column skipped), and writes **`retirement_sets.json`**
  (~1900 sets, committed — so Render ships with data). `GET /api/retirement`
  serves it (mtime-cached); `POST /api/retirement/refresh` re-pulls — LOCAL-ONLY
  (403 on Render; needs `openpyxl`, a user-site dep of `/usr/bin/python3`, NOT
  in requirements.txt). A launchd agent (`com.brickscanner.retirement-refresh`,
  via `refresh_retirement.sh`) re-pulls on the 5th of each month at 06:00 local —
  the sheet updates near the start of each month.

**Bulk scanning fixes — bbox space + full-pile sweep (June 2026):**
- **Root-cause fix:** Brickognize returns bounding boxes in ITS internal resized
  space (the bbox's own `image_width/height`), not the submitted image's. The
  single-scan client always honored that; `identify_multi` didn't — so every
  mask/overlay landed offset toward the origin (user-visible as the box hovering
  the wrong piece), and "masked" pieces kept re-triggering because the paint
  never covered them. Server now rescales each bbox into its image space.
- **Sweep hardening:** dud rounds (overlapped/unclassifiable detections) no
  longer end the loop — they paint a progressively larger patch and continue
  (same-size re-masks provably don't dislodge the detector); only a 5-dud
  streak stops pass 1. **Pass 2** then finds leftover not-background blobs
  (pure-PIL connected components on a coarse grid, `_find_blobs`) and submits a
  tight crop of each — catches pieces the detector never fires on; blob results
  deduped by intersection-over-smaller-box. Regions sorted into reading order.
  5/5 pieces on the synthetic EXIF-orientation-6 test (was 3/5).
- **Frontend:** photo-overlay boxes now %-positioned (immune to iOS layout
  timing — the px-at-onload math also misplaced boxes), plus an "N pieces
  boxed" hint under the photo. Debug trail per pile scan: `/tmp/brk_multi.json`.

**Bulk scanning (June 2026):**
- **Backend:** `identify()` factored into `_brickognize_search()` +
  `_items_from_detected()` (behavior unchanged). New `POST /api/identify_multi`:
  Brickognize's internal endpoint only ever returns ONE `detected_items` entry
  (verified empirically + against their own frontend bundle), so the route loops
  mask-and-rescan — detect the most prominent piece, paint its bbox with the
  border-sampled background colour (Pillow, now in requirements.txt), resubmit;
  stops on no detection, score < 0.25, bbox-IoU > 0.5 vs. an already-found region
  (mask didn't take), or 8 rounds (politeness cap). Mid-loop network failure
  returns the regions found so far with `"partial": true`.
- **Frontend bulk mode** (layers toggle right of the shutter, persisted in
  `localStorage('bulkScan')`): live-scan hits are filed into a **scan tray**
  (`bulkTray`, chips strip under the scan stage) while the camera keeps running —
  conveyor workflow. Dedupe: the same top candidate won't re-add until 2 clear
  ticks (piece left the frame); identical part+colour rows merge into ×N.
  The shutter (or photo capture) in bulk mode sends the frame to
  `/api/identify_multi` — pile scan; each region gets per-bbox colour sampling.
- **Review screen** (`screen-bulk-review`): pile photo with numbered bbox
  overlays, one row per piece (candidate select when Brickognize returned
  alternates, colour select with stud dot, ×N steppers, red `needs-color`
  highlight), "Parts go to" list picker (defaults to the scan tab's selection),
  **Add All** (parts → `/api/add_part` per row, minifigs → `/api/add_minifig`;
  failures stay in the tray). Colour decision logic factored out of
  `showIdentifyScreen` into shared `resolveDetectedColor()`.

**Sets + Part Lists consistency pass (June 2026):**
- **Sets tab now mirrors Figs exactly:** centred copper `.collection-head`
  ("My Sets" + mono stats), pill catalog search (`.list-search-input`, debounced
  450 ms / min 3 chars via `onSetsSearchInput`, Enter forces) replacing the
  boxed label+Find card, and a sort + `⋯` toolbar (`toggleSetsTools`) hiding
  "↻ Refresh prices" (`setPricesBtn`, was a 28px `↻` circle with a forbidden
  `title=`) and "Export CSV" in the overflow row. The My-Sets collapse chevron
  + `_mySetsCollapsed` machinery removed (Figs precedent: the grid IS the screen).
- **Part Lists:** list picker + search restyled as pills; "Manage list" `▶`/`▼`
  text arrows → a rotating SVG caret driven by `[aria-expanded]` CSS (same
  pattern as the scan tab's catalog drawer); `🛒` emoji dropped from the
  BrickLink export button.
- **Shared classes** replace four copy-pasted inline styles: `.collection-head`/
  `.collection-title`/`.collection-count` (Figs + Sets headers) and
  `.toolbar-select` (all toolbar sort selects). Copper officially extends to
  all collection headers (system.md updated).
- **Token sweep in JS-rendered rows:** remaining `color:#3B9EFF`/`color:#fff`
  → `var(--yellow)`/`var(--text)` (white-on-white light-mode bugs in list/
  shopping/set-detail rows); `#debugLabel` keeps `#fff` deliberately (black scrim).

**Identify loading screen: scan your own photo (June 2026):**
- The hand-drawn 3D brick + corner-tick frame + typewriter "IDENTIFYING..." +
  giant percent counter are gone. The loading screen now shows **the captured
  photo itself** in a stage with the scan card's exact dimensions
  (`.loading-stage`, set by `showLoadingScreen`) — the viewfinder appears to
  freeze into your shot — with the azure beam (`.loading-sweep`, fixed colours:
  it sweeps the photo, same theme-independence exception as the scrim chip)
  and the breathing reticle over it, plus a bottom `.scrim-chip` reading
  "Identifying · NN%" (`#loadingPct` simulation unchanged). The live-scan chip
  styling was promoted from `#liveScanning` to the shared `.scrim-chip` class.
- Removed dead weight: the hidden magnifying-glass SVG and its **permanently
  running `animateScan()` rAF loop** (it animated a `display:none` element on
  every frame forever), `animateTypewriter`, and the old gold-tinted beam
  gradient. Net −242 lines.

**Camera-first Scan tab redesign (June 2026):**
- **Hero scan stage.** One fixed-height card (`.scan-area`, `clamp(300px,44vh,400px)`)
  for both states so nothing jumps. Idle: socket + one-line hint. Live: the
  viewfinder fills the card edge-to-edge (`#liveVideo` absolute, cover), with a
  dashed **reticle** (`.scan-reticle`), a "Scanning" **scrim chip**
  (`#liveScanning`, deliberately theme-independent — it sits on the camera feed),
  and a bottom control rail. The stacked Start-Live-Scan/Take-Photo buttons are
  gone: a **shutter** (`shutterTap`) captures the current frame mid-scan
  (`captureLiveFrame`, no confidence gate) or opens the native camera when idle,
  and a side toggle (`.scan-ctl`, video/stop SVGs) starts/stops live scan.
- **Search pill.** Card + label + Search button + "🎤 Add by voice" row replaced
  by one pill (`.search-pill`): leading search glyph, debounced live search
  (`onScanSearchInput`, 450 ms, min 3 chars — short queries skipped so the
  Render API fallback isn't hammered), Enter forces, trailing **mic SVG** opens
  voice-add. Voice modal's 🎤/⏹ emoji also replaced with SVGs swapped by the
  existing `.listening` class.
- **Slim list selector.** "Adding to" label + pill select + one `⋯` button
  (`toggleListActions`) revealing New list / Delete list — the two round
  emoji buttons (and their forbidden `title=` tooltips) are gone.
- **Catalog footer → one tappable mono line** ("Catalog · Updated …", caret) that
  expands a drawer (`toggleCatalogDetails`) holding last-checked + DB size,
  the refresh button, and the changes list; auto-opens while a rebuild runs.
- **Token hygiene.** All `#06121F` on-accent inks → `var(--on-accent)` (was wrong
  in light mode), `#22C55E` → `var(--green)`, JS `background:#3B9EFF` →
  `var(--yellow)`. Drop ring/reticle get a reduced-motion-gated `ringBreathe`
  idle animation; `#screen-scan` bottom padding is safe-area aware.

**Figs tab cleanup (June 2026):**
- **One search bar.** The separate catalog-search box is gone; the collection
  filter input is the only search. Typing live-filters your figs
  (`onFigsSearchInput`); Enter — or the "Search catalog for …" row appended to
  filtered results (`.figs-catalog-cta`) — runs the same query against the
  catalog (`searchMinifigsLocal`, re-pointed at `myMinifigsSearchInput`) to add
  a new fig. Opening a catalog result no longer clears the input, so the grid
  filter survives the identify round-trip. Dead `searchMinifigById` removed.
- **Proper header.** Collapse chevron + `_myMinifigsCollapsed` machinery removed
  (the grid IS the screen now); stats moved to their own mono sub-line with a
  cleaner format (`317 entries · 369 figs · ≈$6,420`). Title + stats centered in
  **copper** — the wordmark's `#C87941`, promoted to a `--copper` token (header
  `h1` now uses it too instead of raw hex).
- **Toolbar slimmed.** Owned 2+ and sort stay; Export CSV + the price-refresh
  button moved into a `⋯` overflow row (`toggleFigsTools`/`#figsToolsRow`).
  Shared `.figs-tool-btn` class replaces the repeated inline button styles;
  `title=` tooltips dropped (iOS long-press never shows them).

**Unified scanner + dedicated Figs tab (June 2026):**
- The Parts/Figs scan modes merged into one **Scan** tab — `/api/identify` always
  returned typed candidates (part vs minifig) and the identify screen already
  branched per item, so the tabs only ever swapped chrome. The scan screen now
  carries a neutral hint, an always-visible "Add to parts list" selector, and a
  **combined search** (`searchScanLocal`) that queries parts + minifigs in
  parallel and renders both groups (each via `renderLocalResults`, so bl_match /
  click behavior is unchanged).
- **Figs** became a browse tab (`screen-figs`, like Sets): minifig search + the
  My Minifigs collection grid moved there from the scan screen. Pull-to-refresh
  wired; mode values renamed to `'scan'|'figs'|'sets'|'lists'`.
- Identify Back/Retake now returns to the tab that opened it (`_identifyReturn`,
  captured in `showIdentifyScreen`; loading/success map to scan) with a matching
  button label, and `goTo()` syncs the tab bar + `mode` when jumping straight to
  a tab screen. Loading screen always uses the brick animation + "Identifying...".

**Collapsible theme groups in My Minifigs (June 2026):**
- In the theme-sorted view, each theme header is now tappable: chevron + group
  collapse/expand in place (no re-render, scroll position holds). Collapsed
  themes persist in `localStorage` (`myMinifigsCollapsedThemes`) across renders
  and reloads, and apply in both the scan-screen section and the Lists-tab view.
- While a search/dupes filter is active every group renders open, so matches are
  never hidden inside a collapsed theme.

**Satellite panels — identify screen lower half (June 2026):**
- Everything below the trading card now matches its depth language: price guide,
  sets, parts, other matches, and the action area become floating "satellite
  panels" on the card's footprint (12px margins, radius 16, surface fill, seam
  border) — no frame/shadow, which stay exclusive to the focal card. Section
  labels moved inside the panels.
- The art-window language repeats in miniature: set-row thumbnails and price
  chips sit on `--socket`-dark insets; "Other matches" tiles became mini-cards
  (seam frame, thumbnail on a socket stage — tapping one swaps the focal card).
  Selected alt-card highlight now uses `--yellow-glow` (was raw rgba).
- Parts-mode photo banner floats too (margins + radius, not full-bleed); the
  owned-bar inner boxes moved to `--surface2` so they read against the panel.
- CSS-only (plus two inline-style tweaks); no JS or markup-structure changes.
- Actions moved up: the action panel (owned bar / quantity + add, Retake Photo)
  now sits directly under the card, before the price/sets/matches panels.

**Trading-card identify screen (June 2026):**
- The identify card is restyled as a trading card ("Pokémon card" feel): floating
  card with a gradient azure holo frame + soft shadow, the name as a title bar on
  top, and the catalog image as the hero — large and centred in an inset **art
  window** with a radial spotlight. The user's scan photo shrinks from the old
  50/50 split to a small corner chip over the art.
- Pure presentation: all element ids (`partName`, `catalogImg`, `userThumb`,
  banners, info rows) are unchanged, so the populate/JS flow is untouched. Only
  behavioural tweak: minifig scans hide the full-width `photoBanner` (the chip
  carries the photo; parts keep the banner for colour verification).
- **Card-flip reveal:** the card turns over (back → front, .65s 3D rotateY) each
  time the Identify screen shows, replacing the old sweep-in. Structure: a
  `.identify-flip` perspective wrapper holds the card (front, backface hidden) +
  a `.identify-card-back` (pre-rotated 180°, pointer-events none) styled as a
  big scan socket in the same holo frame. `perspective()` is inside the keyframe
  transform so no ancestor gains a perspective property and the animation ends
  at `transform:none`. Honors `prefers-reduced-motion`.
- Pattern documented in `.interface-design/system.md` ("Identify focal card").

**Removed the CMF box-code scanner + native iOS shell (June 2026):**
- Dropped the entire CMF (Collectible Minifigure) box-code feature — the **CMF**
  nav tab + `#screen-cmf`, all `_cmf*` frontend JS, the `/api/cmf/*` endpoints
  (`series`/`lookup`/`decode`/`capture`/`captured`) and their libdmtx/Pillow
  decode path, and the `cmf_codes.json` / `.cmf_captured.json` data. Web is the
  focus for now. `pylibdmtx`+`Pillow` dropped from `requirements.txt`; the
  CMF-only `static/zxing.min.js` removed.
- Removed the Capacitor **native iOS shell** (`native/`, `BUILD_IOS.md`) — it
  existed mainly to add on-device Data Matrix scanning for the CMF tab.
- See the entry below for what was removed; revivable from git history if needed.

**Detect BrickLink minifig variants on scan (June 2026):**
- BrickLink has no "variants of" API and Rebrickable carries no BrickLink minifig
  ids, so the only way to know `sw0574a` exists is to ask BrickLink for that exact
  id. New `GET /api/minifig_variants/<bl_id>` takes a minifig id, strips it to its
  numeric base (`_minifig_base_id`), and probes `base`, `base+a…` via the BrickLink
  API (`_probe_minifig_variants`), keeping the ids that exist. Stops after 2
  consecutive misses — letters are contiguous, and this also catches bases whose
  plain id 404s while `…a` exists (e.g. `sw0001` → a/b/c/d).
- Results are cached in local `.bl_minifig_variants.json` keyed by base, with a
  30-day TTL so the list **grows as you scan** and newly-added variants are
  re-detected later. A transient BrickLink outage (empty probe) never clobbers a
  good cache. LOCAL-ONLY (needs BrickLink creds); git-ignored.
- Identify card: a new **Variants** row (`#variantsRow`) shows a chip per sibling
  id (only when the family has >1), tooltip = each variant's own name (`sw0574` =
  "…Hair" vs `sw0574a` = "…Helmet"). Tapping a chip switches the BrickLink id
  (`_switchMinifigVariant` → price + ownership re-check), reusing the variant-entry
  logic below. Loaded async on card open + after a manual ✎ id edit.

**Track BrickLink minifig variants separately (June 2026):**
- BrickLink splits print/mold variants of a minifig with a trailing-letter suffix
  (e.g. `sw0574` vs `sw0574a`) where Rebrickable keeps a single fig (`fig-004079`).
  My Minifigs now tracks these as **separate collection entries** with their own
  quantity / condition / price-paid / BrickLink market price.
- New `_minifig_variant_suffix()` + `_minifig_ckey(fig_num, bl_id)` in `app.py`:
  the suffix-less/base id keeps the bare `fig_num` key (existing entries
  unaffected — backward compatible); a suffixed id keys to `fig_num#<suffix>`.
  Entries now also store the real `fig_num` since the dict key can be composite.
- Owned-minifig routes (`add_minifig`, `remove_minifig_one`, `owned_minifig_status`,
  `/meta`, `/blid`) take an optional `bl_id` (body) / `?bl=` (query) to address a
  specific variant; `owned_minifigs_list` returns the real `fig_num`. The price
  refresh already iterates by collection key, so variants price independently.
- Frontend: the identify card's BRICKLINK ID editor (✎) is now variant-aware —
  typing a suffixed id (e.g. `sw0574a`) re-checks ownership for that variant, so
  "Add to My Minifigs" creates its own row. My Minifigs rows carry `data-fig-bl`
  so reopening / swipe-removing a variant targets the correct entry.

**Subtract-a-Set promoted to its own tab (June 2026):**
- The **Subtract a Set** tool is now a top-level bubble in the Lists tab bar
  (My Lists · Shopping · **Subtract Set**) instead of being buried in the
  *Manage list* accordion. New `#listsModeSubtractView`; `switchListsMode()`
  generalized from a 2-way toggle to a 3-tab map. *Manage list* now holds
  Import/Export/Compare only.

**Subtract-a-Set: dedicated list picker (June 2026):**
- The **Subtract a Set** tool now has its own **"From list…"** dropdown
  (`#subtractListSelect`, populated alongside the Compare selects in
  `_populateCompareSelects`) instead of silently operating on the top
  "Select a list…" selector. `runSetSubtract()` reads this picker, making the
  tool self-contained and clearer about which list it subtracts from.
- Also: updated the private Tailscale URL in `SETUP.md` after migrating to the
  Mac mini (`jefs-macbook-pro` → `jefs-mac-mini.tailbdd458.ts.net`).

**Subtract a set from a parts list (June 2026):**
- New **Subtract a Set** tool in the Lists → *Manage list* section. Enter a set
  number against the selected parts list to preview how many needed pieces that
  set would cover — matched on exact `(part_num, color_id)`, removable qty per line
  is `min(set_qty, list_qty)` — then confirm to decrement the list.
- **Backend:** `GET /api/partlists/<id>/set_overlap?set_num=X` returns the per-line
  breakdown (`list_qty`, `set_qty`, `remove_qty`, `remaining_qty`) + totals; nothing
  is mutated. `POST /api/partlists/<id>/subtract_set` applies the confirmed deltas
  (throttled PUT new qty / DELETE at 0). Bare set numbers normalize to `-1`
  (`_normalize_set_num`); set parts include spares. Shared
  `_fetch_partlist_parts_map()` helper (also now used by list-compare).
- **Frontend:** results modal mirroring the Compare modal (`runSetSubtract` /
  `_renderSetSubtract` / `confirmSetSubtract`); reloads the list on success.

**CMF (Collectible Minifigure) box-code scanning (June 2026):**
- Identify which figure is inside a sealed CMF box by reading the **Data Matrix
  code** on the box bottom (right of the barcode). Recent boxes (Series 25+, 2024
  on) encode a **7-digit number** that maps — via a **per-series, per-region lookup
  table** (no algorithm) — to the figure. Older blind bags carry no readable code.
- **Data:** `cmf_codes.json` (git-tracked) maps each 7-digit code → `{series, name}`
  (both US/Mexico and EU/Czech regional codes). Seeded with **Series 25**
  (both regions) and **Series 29** (US codes; set 71052). **Community-sourced
  (Jay's Brick Blog / BrickNav) and unverified** — confirm against
  a physical box. Add a series by appending code→name entries (see the `_template`
  key in the file).
- **Backend:** `GET /api/cmf/lookup/<code>` resolves a code → figure, enriching it
  with a Rebrickable `fig_num` + image via the offline catalog (`_enrich_cmf_figure`
  → `_local_resolve_minifig`; degrades to name/series only when the catalog is
  absent, e.g. Render). `GET /api/cmf/series` lists series+figures we have data for.
  Table cached with mtime-based reload (`_load_cmf_codes`).
- **Frontend:** a dedicated **CMF** bottom-nav tab (`switchMode('cmf')` →
  `#screen-cmf`) with a live viewfinder + **manual 7-digit entry** fallback; a hit
  shows the figure + **Add to My Minifigs**. The camera starts on tab-enter and is
  released on leave/background (`_cmfEnterTab`/`_cmfStopScan`, hooked into
  `switchMode` + `visibilitychange`).
- **Decoding (iOS-robust):** ZXing (`@zxing/library`, vendored at
  `static/zxing.min.js`) instead of the native `BarcodeDetector` (unsupported on
  iOS Safari). The first cut used `decodeFromVideoDevice`, which grabbed a low-res
  stream and wouldn't read these tiny codes on iOS; replaced with a **manual
  high-res snapshot loop** — open a 1080p rear stream and every 400 ms decode a
  full-resolution frame via the core `DataMatrixReader` (`TRY_HARDER`) on a canvas
  luminance source (`_cmfDecodeFrame`).
- **Capture log (build the table in-app):** an unknown-but-valid 7-digit code opens
  a **tagger** — search the minifig catalog, pick the figure, and the pair is saved
  to a local `.cmf_captured.json` (git-ignored; `POST /api/cmf/capture`,
  `GET /api/cmf/captured`). `/api/cmf/lookup` checks captured after curated, so a
  tagged box is recognised from then on. This is how Series 29 (and beyond) gets
  built from **verified scans** rather than unreliable scraped tables — after the
  BrickNav Series 29 table proved wrong against a physical box (real code `6623274`
  = Cute Witch). LOCAL-ONLY.

**Move Cart tab into Part Lists; declutter the Lists screen (June 2026):**
- Removed the **Cart** bottom-nav tab and folded its Shopping List + Gap Analysis
  into the **Part Lists** screen behind a top **My Lists / Shopping** toggle
  (`switchListsMode`). Separately, the per-list Import/Export/Compare tools were
  collapsed into a single **"Manage list"** expander (`toggleListTools`) so the
  parts grid is the immediate content.

**Fix: manually-saved BrickLink id not shown when reopening a fig (May 2026):**
- A hand-entered `bl_id` persisted to the collection (it showed in the lists/rows)
  but reopening the minifig showed it blank — opening fetches `/api/minifig/<fig>`
  for the id, and Rebrickable supplies none. `owned_minifig_status` now returns the
  stored `bl_id`, and `loadOwnedMinifigStatus` backfills it into the identify view
  (re-renders the BRICKLINK ID row, clears the red flag, loads pricing).

**Stop unsolicited camera-permission prompts (May 2026):**
- Live auto-scan used to call `getUserMedia` on every app open / scan-screen entry
  / resume, which re-prompted for camera permission whenever iOS hadn't persisted
  the grant. Now `syncLiveScan` first checks the permission **silently** via the
  Permissions API (`_queryCamPerm`) and **auto-starts only when it's already
  `granted`** — otherwise it shows the "Start Live Scan" button so the prompt fires
  only on an explicit tap. After the first grant it auto-starts silently on future
  opens; if the grant lapses it shows the button again instead of nagging.
  (Browsers without camera Permissions API → treated as not-granted → tap to start.)

**Manually edit a minifig's BrickLink id (May 2026):**
- The minifig identify card's BRICKLINK ID row now has an ✎ edit control (inline
  text input) so you can type/correct the id by hand — the fix for figs Rebrickable
  gives no BrickLink id for. On save it updates `selectedPart.blId` (so it's
  captured if you then Add), clears the red "no BL id" flag, and refreshes the live
  price panel. If the fig is already owned it persists immediately via
  `POST /api/owned_minifigs/<fig>/blid` (which also clears stored prices on change
  so the next refresh re-fetches). Row rendering factored into `_renderMinifigBlRow`;
  `editMinifigBlId`/`saveMinifigBlId`; price load into `_loadMinifigPriceFor`.

**Fix: Lists-view stale-response race (May 2026):**
- Selecting a Collection (My Minifigs / My Sets) in the Lists dropdown *before* the
  auto-loaded first parts list finished fetching left the grid blank — the slow
  `parts_all` response landed late and overwrote the collection render. Added a
  per-load sequence token (`_listLoadSeq`): every loader/reload captures a seq and
  discards its response if a newer selection has superseded it, so the last
  selection always wins.

**BrickLink price tracking for My Minifigs + daily 5am refresh (May 2026):**
- For every owned minifig that has a **BrickLink id**, the app fetches BrickLink's
  **last-6-month SOLD average** (Used + New) and stores it on the collection entry
  (`price_used`/`price_new`/`price_updated` in `.minifig_collection.json` — the
  local user-data store; the SQLite catalog is read-only). Figs without a
  BrickLink id can't be priced (Rebrickable exposes none) and are skipped.
- Backend: `refresh_minifig_prices()` (iterates the collection, `_bl_sold_price`
  per fig, 0.4s throttle, merges into a fresh read to avoid clobbering concurrent
  edits); `POST /api/minifig_prices/refresh` (threaded manual trigger);
  `/api/owned_minifigs` now returns the stored prices.
- **Daily 05:00 local** via a new launchd LaunchAgent
  (`com.brickscanner.minifig-prices.plist` → `refresh_minifig_prices.sh` →
  `refresh_minifig_prices.py`, logs to `minifig_prices.log`); installed by
  `./install_agents.sh`. **LOCAL-ONLY** (no cron on Render; collection is empty
  there), same pattern as the catalog refresh.
- Frontend: each My Minifigs / Lists-minifig row shows an azure **`BL ~$X`** chip
  (6-mo avg for the fig's condition), and the count line shows an estimated
  **collection value** (`≈$N`, Σ avg×qty). `_figMarketPrice` picks used/new by the
  fig's condition.

**My Sets + My Minifigs in the Lists dropdown (May 2026):**
- The Lists-tab dropdown gained a **"Collections"** optgroup with **"My Sets"** and
  **"My Minifigs"** — selecting either renders that collection in the list view
  with the same live search (sets: name/set #/year · figs: name/fig #/BrickLink id),
  count line, swipe-to-remove, and tap-to-open as the dedicated tabs. Implemented
  via `_listMode` (`parts|minifigs|sets`) + `_listAllMinifigs`/`_listAllSets`,
  `loadMinifigsIntoListView`/`loadSetsIntoListView` + `renderMinifigListRows`/
  `renderSetListRows`. Row markup + wiring extracted into shared
  `_minifigRowHtml`/`_renderMinifigRows` and `_setRowHtml`/`_renderSetRows` (reused
  by `loadMyMinifigs`/`loadMySets`); `swipeRemoveOwnedMinifig`/`swipeRemoveOwnedSet`
  take a per-view reload callback. Pull-to-refresh works here too (the dropdown
  value routes through `loadListContents`).

**"Already in My Minifigs" alert on scan (May 2026):**
- When a scanned/opened minifig is already in the My Minifigs collection, the
  identify card shows a prominent green **"Already in My Minifigs ×N"** banner
  under the name, and a **scan** (captured photo present) also fires an instant
  toast (`✓ Already in My Minifigs — ×N`) so you know during rapid scanning
  without reading the card. Driven off the existing owned-status check
  (`loadOwnedMinifigStatus` → `_renderOwnedMinifigUI`); banner resets per card.
  (Match is by resolved Rebrickable fig_num, so a fig added via search may not
  match a later scan if the catalogs resolve it to different variants.)

**Minifig BrickLink-id row → search link (May 2026):**
- Rebrickable's minifig API exposes **no BrickLink id** (`external_ids` is null for
  figs), so a search-opened minifig had a dead "—" in the BRICKLINK ID row. It now
  renders a **"Search BrickLink ↗"** link (BrickLink minifig catalog search by the
  fig's name) when no id is known. Scanned minifigs / BL-id searches still show the
  real id + copy button. `get_minifig` also now surfaces `external_id` from
  `external_ids` if Rebrickable ever includes one (harmless no-op today).

**Auto-refresh on resume + precise pull spinner (May 2026):**
- **Stale-on-reopen fix:** iOS keeps an installed PWA's page suspended and
  *resumes* it (no reload), so data looked stale until a force-close. Now the
  active screen's data **auto-refreshes when the app returns to the foreground**
  (`visibilitychange` → visible after >2s hidden, plus `pageshow`/bfcache),
  reusing the `_ptrRefreshFn` map (`refreshActiveScreenData`). No more force-close
  for fresh data. (Code/HTML updates still arrive on a cold start via the
  network-first SW; resume refreshes data only.)
- **Pull spinner now tracks the real fetch:** the list loaders
  (`loadListContents`/`loadMySets`/`loadMyMinifigs`/`loadSetDetails` →
  `showSetPartsView`/`loadShoppingList`) return their promise, and pull-to-refresh
  spins until it resolves (with a 400ms floor so instant local loads don't
  flicker) instead of a fixed 750ms.
- **Gated auto-reload on new version:** the app code lives in `index.html`, so the
  server injects a content-hash version (`_app_version`, exposed at `/api/version`
  + `<meta name="app-version">`). On resume the client compares versions
  (`checkForUpdate`); if a new build is deployed it reloads — but only at a **safe
  moment** (`_safeToReload`: not on identify/loading/success, no modal open, not
  mid live-scan), else it marks `_updatePending` and applies it on the next `goTo`
  to a safe screen. Loop-guarded (`_reloading`). So the PWA stays on the latest
  code/data without ever interrupting an in-progress scan.

**Pull-to-refresh on browse screens (May 2026):**
- Pull down from the top (page scroll at 0) on a list screen to reload it: a
  circular spinner (`#ptrIndicator`, fixed/body-level) follows the pull, and
  releasing past ~64px triggers the active screen's reload — **Lists**
  (`loadListContents`, real `parts_all` re-fetch), **My Sets** (`loadMySets`),
  **Cart** (`loadShoppingList`), **My Minifigs** (scan screen, minifig mode →
  `loadMyMinifigs`), and **Set details** (`loadSetDetails` on `currentSetNum`).
- **Vertical-locked** so it never collides with the horizontal back/edge swipe;
  only arms at `scrollTop === 0`, bails if the pull turns horizontal or the page
  is scrolled, and is suppressed while a modal is open (`_overlayOpen`). Added
  `overscroll-behavior-y: contain` to damp the native bounce. Identify/success/
  loading screens aren't refreshable (`_ptrRefreshFn` returns null → inert).

**Swipe-from-left-edge to go back (May 2026):**
- An iOS-style **back gesture**: swiping right from the left edge (start `< 28px`)
  slides the current screen out and navigates to its parent. Maps:
  `screen-identify` → scan (`retakeOrBack`), `screen-set-details` → My Sets,
  `screen-success` → scan. The view follows the finger and fades; releasing past
  ~70px commits, otherwise it snaps back.
- **Only on detail screens** — the top-level tab screens (which carry the
  swipe-LEFT-to-delete rows) are excluded, so the back gesture never collides with
  row removal (opposite direction + different screens). Direction-locked (vertical
  drags still scroll); suppressed while a modal/overlay is open (`_overlayOpen`).
  `goTo` now clears any residual transform so a screen never renders shifted.

**Installable PWA (May 2026):**
- The web app is now an **installable PWA** — "Add to Home Screen" gives a real
  app icon, full-screen standalone chrome (no Safari bars), and an offline shell.
  No native rewrite; works on the phone via the HTTPS Tailscale URL.
- **Manifest** (`static/manifest.webmanifest`, served at `/manifest.webmanifest`
  with `application/manifest+json`): `display: standalone`, portrait, `#0C1014`
  theme/background, 192/512 PNG icons.
- **Icons** generated from the azure-brick logo: `static/app-icon.svg` (square,
  brick on a radial bluish-dark bg) rasterized via macOS `qlmanage` + `sips` →
  `icon-192.png`, `icon-512.png`, `apple-touch-icon.png` (180). Committed assets.
- **Service worker** (`static/sw.js`, served at `/sw.js` with
  `Service-Worker-Allowed: /` + `Cache-Control: no-cache` so it's root-scoped and
  updates promptly): navigations are **network-first** (fresh HTML online, cached
  shell offline); `/static/` is **stale-while-revalidate**; **`/api/` and
  cross-origin (Brickognize/Rebrickable/BrickLink/fonts) are never cached** so data
  stays live. Bump `CACHE` (`brick-scanner-v1`) to invalidate.
- `<head>` gains the manifest link, `theme-color`, Apple standalone meta
  (`apple-mobile-web-app-capable`/`-status-bar-style: black`/`-title`), PNG
  apple-touch-icon, and a guarded `serviceWorker.register('/sw.js')` (secure-context
  only — silently skipped over plain HTTP). Backend: `/sw.js` + `/manifest.webmanifest`
  routes (`send_from_directory` with correct MIME types).

**Live camera auto-scan (hands-free) (May 2026):**
- A **live viewfinder** on the scan screen that grabs a frame every ~1.5s and
  runs `/api/identify`, presenting the result on the first **confident hit**
  (top item `score ≥ 0.55`, so empty frames don't fire) — no button press per
  scan. After a hit it releases the camera and shows the identify screen; going
  back to scan resumes the loop.
- **Requires a secure context** (HTTPS or `localhost`) — browsers block
  `getUserMedia` over plain HTTP, so on the **phone over Tailscale `http://`**
  the camera is unavailable and it falls back to the existing "Take Photo"
  capture flow (the Live Scan button is hidden where unsupported). To get it on
  the phone, enable HTTPS on the tailnet (`tailscale serve`). Works on desktop
  `localhost` + Render now.
- Implementation (`templates/index.html`): `<video id="liveVideo">` viewfinder +
  scanning pulse in `.scan-area`; `startLiveScan`/`stopLiveScan`/`liveTick`
  (canvas frame → JPEG blob → identify, single-flight via `_liveBusy`),
  `toggleLiveScan` (persisted `localStorage 'liveScan'`), and `syncLiveScan()`
  hooked into `goTo` + `switchMode` + `load` + `visibilitychange` to start the
  camera only while the scan screen is showing and release it otherwise. No
  backend changes (`/api/identify` already returns per-item `score`).

**Swipe-left to remove one — Parts, My Sets, My Minifigs (May 2026):**
- Rows in the **parts list (Lists tab)**, **My Sets**, and **My Minifigs** now
  support an iOS-Mail-style **swipe-left to reveal a red "Remove 1" button**
  (tap it to decrement by 1; deletes the row at 0). The +/− buttons stay too.
- One reusable helper `makeSwipeRemovable(rowEl, onRemove, label)` wraps any row
  (`.swipe-wrap` clips; `.swipe-fg` slides over an absolutely-positioned
  `.swipe-remove-btn`). Direction-locked touch handling (`touch-action: pan-y`,
  8px lock threshold) so vertical scrolling still works; only one row opens at a
  time (`closeOpenSwipe`). For rows with a tap action (sets/minifigs) the tap is
  swallowed when swiped/open via `consumeSwipe(rowEl)`.
- Parts reuse the existing decrement logic: `adjustListPart` was refactored to a
  shared `mutateListPart(delta, partNum, colorId, row, restore)` (also removes the
  `.swipe-wrap` ancestor at qty 0; keeps the in-memory search list + count in
  sync). Sets/minifigs call `swipeRemoveOwnedSet` / `swipeRemoveOwnedMinifig`
  (`remove_set_one` / `remove_minifig_one`), then reload the browse list.

**My Minifigs — local collection + condition/price (May 2026):**
- **Minifigs now have a real owned collection** (quantity + Used/New + price paid),
  mirroring My Sets. **Key constraint:** Rebrickable's `/users/{token}/minifigs/`
  is **read-only** (`GET, HEAD, OPTIONS` only; no per-item endpoint — it just
  aggregates the minifigs inside owned sets), so the prior "Add to My Minifigs"
  (POST to that endpoint) actually got a 405 and never worked. The whole
  collection therefore lives **locally** in `.minifig_collection.json` keyed by
  fig_num (`{quantity, condition, price_paid, name, img_url}`), git-ignored.
  **LOCAL-ONLY** (ephemeral on Render), like the set metadata.
- Backend (all local, no Rebrickable calls): `add_minifig` (merge qty + store
  name/img), `remove_minifig_one` (decrement, delete + drop metadata at 0),
  `owned_minifig_status`, `owned_minifigs/<fig>/meta` (no-op if not owned),
  `owned_minifigs` (name-sorted list). Generic JSON helpers `_load_meta` /
  `_save_meta` / `_clean_meta` are now shared with the set metadata.
- **Identify screen (minifig):** the owned bar replaces the generic add button +
  quantity input — "+ Add to My Minifigs" → an "In My Minifigs ×N" stepper plus a
  Used/New toggle and `$` price (autosave), exactly like set-details
  (`_renderOwnedMinifigUI`, `addMinifig`, `removeMinifigOne`, `setOwnedMinifigCondition`,
  `saveOwnedMinifigMeta`). Adding no longer routes to the success screen — it flips
  in place with a toast, matching sets.
- **My Minifigs browse list** (minifig mode on the scan screen, collapsible like My
  Sets): thumbnail, name, fig#, **BrickLink id**, ×N + condition/price; tapping a row
  reopens the minifig on the identify screen (its "details") to edit (`loadMyMinifigs`,
  `toggleMyMinifigs`, `retakeOrBack`). The BrickLink id (`bl_id`) is captured into the
  collection entry on add (from `selectedPart.blId`); entries added before this shows
  no id until re-added.

**My Sets — condition + price-paid tracking (May 2026):**
- Each owned set can now record a **condition** (Used/New) and **price paid**.
  Rebrickable's owned-sets API only stores quantity, so this metadata lives in a
  local `.set_meta.json` keyed by set_num (git-ignored). **LOCAL-ONLY:** Render's
  filesystem is ephemeral, so it stays empty there (public site shows blanks);
  one value per set, regardless of quantity.
- Backend: `POST /api/owned_sets/<set_num>/meta` (normalizes input — invalid
  condition → null, price coerced to float, an all-null body clears the entry);
  `owned_set_status` + `owned_sets` now include `condition`/`price_paid`; fully
  removing a set (`remove_set_one` → qty 0) also deletes its metadata.
- **Set-details screen:** a purchase bar under the qty stepper (shown only when
  owned) with a `Used`/`New` toggle (tap the active pill again to clear) and a
  `$` price input; both autosave (`setOwnedCondition`, `saveOwnedMeta`,
  `_renderOwnedMeta`). **My Sets list** rows show the condition + price.

**Owned Sets — "My Sets" collection (May 2026):**
- Track which sets you own (the user's Rebrickable set collection,
  `/users/{token}/sets/` — syncs with rebrickable.com, separate from the
  loose-parts inventory). Backend: `add_set` / `remove_set_one` (merge/decrement
  like `add_part`), `owned_set_status`, `owned_sets` (list).
- **Set-details screen:** an "+ Add to My Sets" button that becomes an "In My
  Sets" bar with a `− ×N +` stepper once owned (`loadOwnedSetStatus`,
  `addOwnedSet`, `removeOwnedSetOne`).
- **Sets tab:** a "My Sets" section below the search lists owned sets (thumbnail,
  name, set#, year · pcs, `×N owned`), each tappable → set-details
  (`loadMySets`, loaded on `switchMode('sets')` + on back from set-details).

**UI Redesign — "sorting station" + Inter (May 2026):**
- Full visual overhaul via the `interface-design` plugin, captured in
  `.interface-design/system.md`. Direction: a tidy LEGO **sorting station** —
  precise + tactile + quietly playful, with the part photos carrying the colour
  while the chrome is a neutral tray.
- **Palette remapped onto the existing tokens** (names kept — `--yellow` is still
  the accent, now azure `#3B9EFF`): **bluish-gray elevation** (LEGO's real
  structural neutral, same hue as the accent) `--bg #0C1014` → `--surface` →
  `--surface2` → `--surface3`, `--socket` for inset inputs, low-opacity bluish
  seam borders, four-level ink. The whole app re-skins because everything routes
  through these vars.
- **Type → Inter** (display/body; clean, neutral, Porsche-Next-like) + **Space
  Mono** retained for catalog data only.
- **Signature: the stud.** Scan target is now a **baseplate socket** (dashed azure
  drop-ring + stud-grid texture), not a magnifying glass. Every colour swatch
  (`.color-dot`, `.swatch-btn .dot`, `.color-swatch`, `.part-item-color-dot`) gets
  the glossy ABS **stud sheen** via a shared `::after` + `--stud-sheen`.
- **All five tab icons** unified to inline monochrome SVGs (`.tab-ico`/`.tab-brick`,
  `fill: currentColor`) — no more emoji; Sets is two scattered bricks.
- Swept all raw hex in CSS **and** JS-generated markup onto the token scale
  (Set-details + Cart/gap-analysis included); removed the global dot-grid texture
  and the harsh 2px accent header rule. **Presentation-only — no behaviour changed.**

**List live search + colour-specific list images (May 2026):**
- Lists view gained a **search box that filters as you type** (part #, name, colour;
  `N of M parts` count). Full list pulled once into memory via the new lightweight
  `GET /api/partlists/<id>/parts_all` (throttled paging, no per-part image fan-out);
  `renderListParts`/`filterListParts` filter in memory. Replaced Load-More pagination.
- `parts_all` overlays **colour-specific images from the local catalog**
  (`_local_part_color_imgs` over `part_colors`, derived from the bulk dump's
  `inventory_parts.img_url`, ~94% coverage, **zero API calls**), falling back to the
  generic part image; graceful on Render (no DB).

**Voice quick-add + iOS lazy thumbnails (May 2026):**
- "Add by voice" gained a persisted **Quick add** toggle: adds spoken parts straight
  to the selected list with no confirm card and re-arms the mic for rapid entry;
  falls back to the confirm card when no list/colour is available.
- `lazyLoadImages()` (IntersectionObserver, `data-src`) on the set-details
  Parts/Minifigs lists — fixes the iOS "?" broken-image flood on large sets (e.g.
  Rivendell, ~991 parts) caused by rendering hundreds of `<img>` at once, and the
  unreliable native `loading="lazy"` for dynamic rows.

**Sold price for sets + minifigs (May 2026):**
- BrickLink **last-6-months sold price** (Used + New: avg, min–max range, # sales)
  shown for minifigs (existing identify panel) and now **sets** (new panel on the
  set-details screen). Uses BrickLink's `guide_type=sold` price guide.
- Backend: shared `_bl_sold_price(item_type, item_no)` helper (both U/N); `GET
  /api/set_price/<set_num>` (SET type; bare numbers default to `-1`, matching
  BrickLink/Rebrickable set ids like `75300-1`). `minifig_price` refactored onto it.
- Frontend: `_renderSoldPriceCards()` shared renderer; set-details fetches/render
  into `#setPriceSection`/`#setPriceGrid`. (eBay was considered but its sold-data
  API is approval-gated + 90-day only; BrickLink is LEGO-specific and already
  integrated.)

**Export to BrickLink Wanted List (May 2026):**
- Lists screen → "🛒 Export to BrickLink Wanted List" builds the selected parts
  list as BrickLink Wanted List **XML** (upload format: `<ITEM><ITEMTYPE>P…<ITEMID><COLOR><MINQTY>`)
  in a modal with **Copy** / **Download** + upload steps (BrickLink → Want → Create
  Wanted List → Upload).
- `GET /api/partlists/<id>/bricklink_wanted` pages the whole list from Rebrickable
  and converts each entry: **part_num → BrickLink item id** (`_rb_part_to_bl`, reverse
  of `bl_aliases`; falls back to the part_num) and **color id → BrickLink color id**
  (`_rb_color_to_bl` via the new `bl_colors` table). Returns `{xml, item_count,
  total_qty, unmapped_colors}`; colors with no BrickLink mapping are emitted without
  `<COLOR>` ("any color") and counted.
- `bl_colors` (rebrickable color id → BrickLink color id) is harvested in
  `build_brick_db` (`harvest_bl_colors`, 1 request, ~216/275 colors mapped), same
  every-build/graceful pattern as `bl_aliases`. (Minifig lists can't be exported —
  Rebrickable exposes no BrickLink minifig ids to reverse-map.)

**Add by Voice (May 2026):**
- "🎤 Add by voice" button on the Parts scan screen opens a modal where you speak
  (or dictate/type) a **part number, color, and quantity** — e.g. *"3068b dark green 2"*.
- **One-tap mic** uses the Web Speech API (`SpeechRecognition`/`webkitSpeechRecognition`),
  shown only in a **secure context** (HTTPS or `localhost`). Over plain HTTP (e.g. the
  phone on the tailnet) the mic is hidden and the text box + **keyboard dictation** are
  used instead — same parser, works everywhere. (Enable HTTPS on the tailnet via
  `tailscale serve` to get the one-tap mic on the phone.)
- `parseVoiceInput()` extracts: **color** (longest catalog color-name phrase match
  against the `colors` list), **quantity** (explicit `quantity/qty/times/x N`, or a small
  trailing number; number-words supported), and **part number** (the normalized
  remainder). The part is resolved **BrickLink-first** via `GET /api/resolve_part/<id>`
  (`_local_resolve_part`: exact → `bl_aliases` BrickLink map → mold heuristic) since
  users speak BrickLink numbers (e.g. "3068" → 3068b); only if that 404s does it fall
  back to fuzzy name/number search (`_pickVoicePart`).
- Reuses the **identify screen as the confirm card**: `submitVoiceText()` → `openPartFromSearch()`
  (now awaited) → pre-fills quantity and `applyColor(parsed color)`; the user reviews and
  taps the existing "Add to List" (so list selection / picker behavior is unchanged).

**Catalog Change-Tracking — renames, set contents & tables (May 2026):**
- `_diff_catalog` now records, per category, **added / removed / renamed** items
  (rename = name changed for the same `part_num`/`fig_num`/`set_num`), plus
  **set-content changes** — sets whose inventory composition changed, detected via
  a cheap per-set signature `(distinct part/color lines, total qty)` from
  `inventories`⋈`inventory_parts` (`_set_signatures`). This is what Rebrickable's
  frequent `inventories`-table updates actually represent.
- The `.catalog_changes.json` record **always includes the updated `tables` list**
  and is written on every refresh that had a prior catalog to diff — even with no
  item/content changes — so the footer can still show *which* tables updated.
  (Previously an inventories/themes-only update wrote nothing → footer showed only
  "Catalog updated (N tables)" with no detail.)
- Frontend `_renderChanges` renders an "Updated tables" line, blue `~` rename rows,
  and a "Set contents changed (N)" group; the panel shows whenever there's any
  change or table info. New CSS: `.cc-sign.ren`, `.cc-tables`.

**Private Access via Tailscale + autostart (May 2026):**
- App reachable privately from a phone over **Tailscale** (`0.0.0.0:5001` on the
  tailnet, WireGuard-encrypted, no public exposure / ngrok / port forwarding) —
  see **Private Access (Tailscale + autostart)** in `SETUP.md`.
- `start.sh` drops the ngrok tunnel and prints the auto-detected Tailscale URL
  (ngrok static-domain config left intact for optional reuse).
- `com.brickscanner.app.plist`: launchd LaunchAgent runs the Flask server at login
  and restarts it on crash (`KeepAlive`); local-only. Logs to `app.log` (git-ignored).
- Daily catalog-refresh job moved **04:30 → 07:30 ET** (just after Rebrickable's
  ~07:12 ET catalog update); launchd uses local time so it tracks DST.

**Image Preview — set details + identify screen (May 2026):**
- In the Sets tab, tapping a part or minifig thumbnail in a set's Parts/Minifigures
  list opens the full-screen image modal. Reused `openImageModal` with an optional
  `linkType` arg so minifigs link to BrickLink `M=` catalog pages (parts keep `P=`).
- On the **identify screen**, the identified item's catalog image (`#catalogImg`) is
  tappable too (set in `populateCardInfo`) — opens the same modal, reading the live
  src so color-specific part images enlarge, with the BrickLink link using `M=`/`P=`
  by item type.

**Offline Catalog Search (May 2026):**
- New local search over the full Rebrickable catalog (~62k parts, ~16k minifigs, ~26k sets) backed by a local SQLite DB — instant and not subject to the 60 req/min Rebrickable rate limit
- `build_brick_db.py` loads the `Brick Parts/` CSV dump into `brick_parts.db` (parts, minifigs, sets, colors, categories, themes, inventories; derives per-part thumbnails and distinct part/color combos). It also **harvests a BrickLink→Rebrickable part-id map** (`bl_aliases` table) via `harvest_bl_aliases()` — Rebrickable's parts *list* endpoint includes `external_ids` inline, so the full map is ~63 throttled requests (~1-2 min), not 62k. Runs on **every build** (local + Render, so production resolves identically); graceful — with no `REBRICKABLE_API_KEY` or on API failure the table is left empty and resolution falls back to the identity/mold heuristic + live API.
- Backend: `GET /api/local/search?q=&type=parts|minifigs|sets` — prefers the local DB, **falls back to the live Rebrickable API when the DB is absent** (`_api_search_fallback()`), returning `"source": "offline" | "api"`
- Frontend: parts/minifigs scan screens now search by **name or number** (results dropdown, `.local-result`); Sets tab search repointed from `/api/search_sets` to the local DB. Clicking a part/minifig result opens the existing identify screen (view + add-to-list); set results open the existing set-details screen
- **Data-source badge** (`sourceBadge()` / `.source-badge`): a sticky header above search results showing 🟢 "Offline catalog" (local DB, no quota) or 🟡 "Rebrickable API" (live fallback) + result count
- **Scanning also uses the local catalog** when present (each falls back to the live API if the DB is absent or has no local data for that item):
  - `/api/identify` resolves BrickLink→Rebrickable part ids (`_local_resolve_part`: (1) exact identity match — covers most standard parts; (2) **authoritative `bl_aliases` lookup** — a full BrickLink→Rebrickable map harvested from Rebrickable's `external_ids`, picking the most-common mold when a BrickLink id maps to several; (3) **mold-variant heuristic fallback** — a bare BrickLink number like `3068` maps to the most-common suffixed Rebrickable mold `3068b` "with groove" via inventory frequency) and minifig fig_nums by word overlap (`_local_resolve_minifig`) locally — previously up to ~5 *un-throttled* Rebrickable calls per scan. **Candidate color ids** are also resolved by NAME via the local catalog (`_local_color_id_by_name`): Brickognize returns BrickLink-namespaced color ids (e.g. `color-156` = Medium Azure ≠ Rebrickable 156), so the numeric id is replaced with the correct Rebrickable id by matching the color name.
  - **Shared candidate colors:** Brickognize predicts the scanned object's colour but only attaches `candidate_colors` to some part guesses. `/api/identify` shares the first non-empty colour shortlist across *all* detected items, so a mis-ranked primary part (e.g. a 6×6 tile guessed over the real 2×2) still carries the azure shortlist — otherwise the matcher falls back to that part's full palette and picks a wrong nearby colour the object isn't (azure → Dark Turquoise when the mis-ranked part doesn't come in Medium Azure).
  - `/api/colors` (+ `/api/colors-hybrid`) → `_local_all_colors` (full ~275-color list, instant, no quota). **Critical for color matching:** the live Rebrickable colors fetch is rate-limited and degrades to a tiny 45-color `FALLBACK_COLORS` that omits Medium Azure and most specialty colors — when that happened, the frontend's name-based candidate mapping dropped those colors and auto-selected a wrong nearby color (e.g. azure → Blue). Local-first fixes this.
  - `/api/part_colors/<part_num>` → `_local_part_colors` (color picker + accurate `num_sets` from inventories)
  - `/api/minifig_sets/<fig>` → `_local_minifig_sets`; `/api/minifig_parts/<fig>` → `_local_minifig_parts`
  - Still live (cannot be local): photo recognition (Brickognize), minifig pricing (BrickLink), and all user-inventory calls (`partlists`, inventory checks, `part_in_lists`)
- `Brick Parts/` and `brick_parts.db` are git-ignored (local dev only)

**Frontend Redesign (May 2026):**
- Complete visual overhaul of `templates/index.html` — all JS and functionality preserved
- **Design system:** CSS custom properties (`--yellow`/`--bg`/`--surface` etc.), Google Fonts (Barlow Condensed + Barlow + Space Mono)
- **Color scheme:** Azure blue (`#0080FF`) as primary accent replacing `#0072CE`; deep black background (`#080808`) with subtle stud-grid dot texture
- **Mode tabs:** Compact pill buttons; active tab gets solid blue fill
- **Loading screen:** CSS scan-beam animation replaces SVG animation visually; SVG kept hidden in DOM for JS compat; 2×4 LEGO brick SVG with proper 3/4 isometric perspective, 8 studs, radial gradient dome highlights
- **Styling patterns:** Uppercase Barlow Condensed labels, Space Mono for numbers/IDs, corner-bracket decorators on scan area
- **Mobile overflow fix:** `html/body { overflow-x: hidden }`, `.file-input-row` uses `flex-wrap` so file input takes full-width line and buttons wrap below — prevents horizontal scroll on narrow iPhones

**Cross-List Inventory Tracking (May 2026):**
- New endpoint `GET /api/part_in_lists/<part_num>/<color_id>` — Shows which lists contain a scanned part with quantities
- "Found in:" section displays on identify screen after selecting a color
- Quick +/− buttons on each list to adjust quantities without navigating away
- Quantities update instantly with visual feedback

**Minifigure Parts UI Improvements (May 2026):**
- Minifigure parts section now expandable/collapsible with arrow toggle (▶ → ▼)
- Parts display in horizontal layout: image left, text right (cleaner and more scannable)
- "Add Parts" button moved to quantity row (more prominent, easier to reach)
- Parts section collapsed by default to reduce visual clutter

**List Selection & Modal Improvements (May 2026):**
- "No list selected" option added to scan screen dropdown — users can deselect lists
- List picker modal only appears when needed:
  - If default list is selected: adds part directly without modal
  - If no list selected: shows modal to choose list
- Both list picker modals now support creating new lists inline:
  - "+ Create New List" button in modal toggles creation form
  - New list automatically selected after creation
  - Available for both regular parts and minifigure bulk add

**Inventory Status & Management (May 2026):**
- Added inventory checking: When a user selects a color on the identify screen, the app queries if that part/color is already in the selected list
- Shows inventory status UI with current quantity and "Remove 1" button for quick decrements
- Added `GET /api/partlists/<id>/parts/<part_num>/<color_id>` endpoint for checking specific part/color existence
- Added `POST /api/remove_part_one` endpoint to decrement or delete items
- Enhanced list view with +/- buttons for quick quantity adjustments (green for add, red for remove)
- Implemented color-specific image caching with `PART_COLOR_IMAGE_CACHE` to improve performance and accuracy

**Dark Mode (May 2026):** Complete CSS color palette swap from light theme to dark:
- Body: #f2f2f7 → #0a0a0a | Text: #111 → #fff
- Cards: #fff → #1a1a1a | Secondary: #f2f2f7 → #222
- Borders: #ddd → #444 | Blue accent preserved (#0072CE)

**Image URL Fix:** Rebrickable `part_img_url` now used for parts (fallback to BrickLink) to avoid dead image links. Color-specific images are now cached for better performance.

**Quantity Reset:** Moved to start of identify screen to prevent async rendering timing issues on iOS Safari.

**Sets Search Results Overflow Fix (May 2026):**
- `setSearchResults` div was `position:absolute` inside `.sets-search-card` (`position:relative`)
- `.screen` has `overflow-x:hidden`, which Safari treats as creating a new overflow context — clipping absolutely positioned descendants
- Fix: moved `setSearchResults` outside the card as a sibling div in normal document flow; removed `position:relative` from `.sets-search-card`

**Rate Limiting & Security Improvements (May 2026):**

*Rate Limiting (60 req/min compliance):*
- Implemented request throttler to enforce 1 request/second to Rebrickable API
- Added `throttle_rebrickable_request()` function that delays requests as needed
- Created `rebrickable_get()` wrapper for all Rebrickable API calls
- Updated all key endpoints to use throttled function:
  - `/api/partlists` — Uses throttled request
  - `/api/colors-hybrid` — Pagination respects rate limit
  - `/api/partlists/<id>/parts` — Pagination with per-page delays
  - `/api/part/<part_num>` — Single-part lookups throttled
  - `/api/part_colors/<part_num>` — Color list fetches throttled
  - `/api/minifiglists` — Minifig list loads throttled
- Frontend pagination delay increased from 500ms to 1200ms for gap analysis
- Rate limit counter shows usage per minute in logs: `⏳ Rate limit: waiting X.XXs (N/60 requests used)`

*Security Fixes (XSS prevention):*
- Added `escapeHtml()` utility function to safely escape HTML special characters
- Fixed XSS in error messages by escaping API responses before `innerHTML` insertion
- Replaced weak inline `onclick` handlers with event listeners for set search results
- Set names and URLs now stored in data attributes and escaped before rendering
- Image URLs validated with onerror fallback to prevent protocol injection
- Rate limit status codes (429/503) now preserved from API instead of converted to 200

**Implementation details:**
- All Rebrickable API calls go through `rebrickable_get()` which applies throttling
- Backend automatically sleeps before each request to maintain 1 req/sec average
- Request counter tracks per-minute usage with automatic reset
- Frontend error messages safely escape API response text
- Event-based DOM updates prevent attribute injection vectors
