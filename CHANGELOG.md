# Changelog

History of notable changes to Brick Scanner. Newest first. (Moved out of
`CLAUDE.md` to keep that file lean — see git history for full diffs.)

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
