# Brick Scanner — Design System

> Mobile web app (Flask + vanilla JS). All UI lives in `templates/index.html`
> (`<style>` block + JS-generated markup). No frameworks, no build step.

## Direction & Feel

**Sorting station.** The user is an adult LEGO collector at a table of dumped
bricks, filing pieces one at a time — pull a piece, identify the mould and
*colour*, shelve it, repeat. The colour call is the hard part and the whole point.

Feel: **precise + tactile + quietly playful.** Precise like LEGO's clutch system
(parts snap to a grid, nothing approximate); playful because it's a toy and a hobby.
**The bricks are the colour** — the part photos carry all the vividness, so the UI
chrome is a neutral tray that lets them pop, never competing.

## Depth

**Surface colour shifts** are the primary depth system (bluish-gray elevation),
**borders (seams) for separation**, and **soft shadow only on true overlays** +
the single focal "object" card on the Identify screen. Don't add shadows to list
rows, inputs, or section cards — let the surface tint do the work.

## Palette

LEGO's real structural neutral is **bluish gray**, not neutral gray — so the whole
elevation scale is tinted toward azure (same hue as the accent; only lightness shifts).

Tokens live in `:root`. Names are historical (`--yellow` is the azure accent — do
NOT rename, it's referenced everywhere). Always use `var(--token)`, never raw hex —
in CSS *and* in JS-generated inline styles.

| Token | Value | Role |
|---|---|---|
| `--bg` | `#0C1014` | page / baseplate |
| `--surface` | `#141A22` | card surface |
| `--surface2` | `#1B2330` | raised / hover |
| `--surface3` | `#232E3D` | inputs, secondary buttons, thumbnails |
| `--socket` | `#080B0F` | inset (the scan socket centre) |
| `--border` | `rgba(150,180,215,0.10)` | standard seam |
| `--border-bright` | `rgba(150,180,215,0.20)` | emphasis seam |
| `--text` | `#EAEEF4` | primary ink |
| `--muted` | `#9EAAB9` | secondary ink / labels |
| `--muted2` | `#697686` | tertiary / placeholder |
| `--yellow` (accent) | `#3B9EFF` | azure — the single accent |
| `--yellow-glow` | `rgba(59,158,255,0.12)` | accent tint |
| `--green` | `#46C97E` | in-inventory / success (meaning only) |
| `--red` | `#F0564B` | remove / destructive (meaning only) |
| `--stud-sheen` | `radial-gradient(circle at 38% 30%, rgba(255,255,255,.34), transparent 55%)` | ABS gloss on stud chips |

One accent (azure). Green/red are semantic only. Never decorate with colour.

## Typography

- **Inter** (`--font-display` / `--font-body`) — UI + headings. Clean, neutral,
  high-legibility (Porsche-Next-like). Personality stays out of the way so the
  brick photos and colours lead.
- **Space Mono** (`--font-mono`) — **catalog data only**: part numbers, element/
  colour ids, quantities (`×N`), prices, sizes, dates. If it's a number from the
  catalog, it's mono. (Provides a deliberate "machine-readable" contrast to Inter.)

## Spacing & Radius

- Base unit **8px** (deliberately the LEGO stud pitch / 8mm module): 4 / 8 / 12 / 16 / 24 / 32.
- Radius: `--radius-sm: 8px` (inputs, buttons, chips), `--radius: 12px` (cards, modals).

## Signature: the stud

The stud is a real UI element, not decoration. It must appear in multiple places:

1. **Scan target = baseplate socket** (`.scan-socket`): a recessed radial-gradient
   circle with a dashed azure "drop ring" (`::after`) over a subtle stud-grid
   texture (`.scan-grid`) + azure corner registration ticks. NOT a magnifying glass.
2. **Colour swatches = glossy stud-top chips.** Every colour dot/swatch
   (`.color-dot`, `.swatch-btn .dot`, `.color-swatch`, `.part-item-color-dot`) gets
   the ABS sheen via a shared `::after` overlay using `--stud-sheen` (works over the
   inline `background:#rgb`). Picking a colour should feel like picking a brick.
3. Pair colours with LEGO's own names (Dark Azure, Reddish Brown) + the catalog id in mono.

## Component patterns

- **Tab bar** (`.mode-tabs`): 4 tabs (Scan / Figs / Sets / Part Lists), each an inline **monochrome SVG** (`.tab-ico` /
  `.tab-brick`, `fill: currentColor`) + label. NEVER emoji or `<img>` — currentColor
  makes them recolour for active state automatically. Active tab = solid azure pill
  (`.mode-tab.active`), white icon+label.
- **Buttons:** primary = solid azure (`.btn`, `.search-btn`), white text. Secondary =
  `--surface3` + seam border, muted text.
- **Inputs / select:** `--surface3` bg, seam border, azure focus border. Mono font for
  search-by-number fields.
- **List row** (`.part-item`): thumbnail on `--socket` inset · name · stud colour-dot +
  LEGO colour name + azure part# (mono) · `−` / `×N` (mono) / `+` steppers
  (`.list-adjust-btn` red/green).
- **Identify focal card:** trading-card treatment — a floating card with a gradient
  azure "holo" frame (gradient border-box under a surface padding-box, 7px border,
  radius 20), title bar above a centred **art window** (catalog image on a dark
  spotlight stage, the scan photo as a small corner chip), info rows below as the
  card text. The one element allowed a soft shadow — it's the physical card you're
  holding. Revealed with a 3D card-flip (`.identify-flip` + `cardFlipIn`): the back
  face is a big scan socket (dashed azure drop ring) in the same holo frame;
  `perspective()` lives inside the keyframe transform so the animation ends at
  `transform:none` (no stray containing blocks). Reduced-motion disables it.
- **Satellite panels** (identify screen below the card): price / sets / parts /
  other-matches / actions are floating panels on the card's footprint — 12px side
  margins, radius 16, `--surface` fill, 1px seam — **never** the holo frame or
  shadow (focal card only). Section labels live inside the panels. The art-window
  language repeats in miniature: set thumbs + price chips sit on `--socket` insets,
  and each "other match" is a mini-card (seam frame, thumb on a socket stage).
- **Header:** logo mark + wordmark, 1px seam underline (NOT a heavy accent rule).

## Avoid (regressions to catch)

- Raw hex in CSS or JS inline styles — always `var(--token)`.
- Emoji icons in chrome (tabs/nav). Use the inline-SVG set.
- Magnifying-glass / camera-viewfinder scan metaphor — it's a baseplate socket.
- Flat rectangular colour swatches — they must be glossy studs.
- Global dot-grid texture or heavy accent borders — quiet bluish layering instead.
- Shadows on rows/inputs/section cards.

## Notes

- Local-only catalog features degrade gracefully; redesign is pure presentation,
  no behaviour changed.
- Verify visual changes by rendering representative markup against the live
  `<style>` (extract it from `curl http://127.0.0.1:5001/`) + headless Chrome
  screenshot — no JS automation tooling is installed.
