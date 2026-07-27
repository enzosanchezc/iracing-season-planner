# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-page, dependency-free web app (`index.html`) for planning which iRacing series to race
each week, cross-referenced against the cars/tracks the user owns. There is no build step, no
package.json, and no test suite — everything lives in one HTML file with inline `<style>` and
`<script>`. Two standalone Python scripts support it: one converts iRacing's schedule PDF into the
JSON the app reads, the other serves the app locally with real autosave.

## Running it

```
python serve.py                 # serves index.html at http://127.0.0.1:8765, autosaves to planner-settings.json
python serve.py --port 9000     # if 8765 is busy
python serve.py --tidy          # fold sync-conflict copies (e.g. "planner-settings 2.json") back into one file
```

There is no build/lint/test command — it's static HTML/CSS/JS. Verify changes by opening the app
in a browser (via `serve.py`, not `file://`, so `season.json` and the save API are reachable) and
exercising the UI directly.

Regenerating the season schedule from an iRacing PDF:

```
pip install pdfplumber
python season_to_json.py SeasonSchedule.pdf -o season.json     # always use -o on Windows, never "> season.json"
                                                                 # (PowerShell redirection writes UTF-16, which the app can't parse)
```

`FREE_CARS` / `FREE_TRACKS` near the top of `season_to_json.py` are the only thing that needs
hand-editing each season — iRacing's current free/base content list, from iracing.com/membership.

## Architecture

### The three files that matter

- **`index.html`** — the entire application: markup, CSS, and a single `<script>` block with all
  logic. No framework, no modules, no build step. Treat it as one big JS file with HTML/CSS
  attached.
- **`season_to_json.py`** — a PDF scraper (pdfplumber) that turns iRacing's "Current Race
  Schedule" PDF into the compact `season.json` schema the app consumes (see below). It reconstructs
  table rows from word x/y positions because the PDF has no real table structure, then groups lines
  into series → weeks.
- **`serve.py`** — a zero-dependency local HTTP server (stdlib only) that serves the static files
  and exposes one JSON API endpoint, `/planner-api` (GET reads, PUT writes) backed by
  `planner-settings.json` in the same folder. Binds to 127.0.0.1 only; touches nothing outside its
  own directory.

### season.json schema (produced by season_to_json.py, consumed by index.html)

Deliberately compact (short keys, integer indices instead of repeated strings) since it's inlined
into saved copies of the page:

```
{ built, season,
  cars: [name...], tracks: [name...],
  series: [{ n:name, c:class(A-D/R), t:type, l:min licence, r:race-times text, f:fixed?, m:team? }],
  rows:   [{ s:series-index, w:week#, a:start-date, b:end-date, t:track-index, y:layout suffix,
             c:[car-index...], d:length }],
  freeCars: [index...], freeTracks: [index...] }
```

`rows` is the join table: one row per series-per-week. `a`/`b` are `[start, end)` date bounds used
to find "what's live on date X". Car/track ownership is stored by **name**, not index, in saved
state — so re-running `season_to_json.py` on a new season's PDF and reloading carries ownership
over automatically via name matching (`findSeries`/`DATA.cars.indexOf`/etc. in index.html).

### index.html internals

- `DATA` holds the season (`window.__SEASON__`, `season.json`, or an embedded fallback — see boot
  order below). `state` holds everything user-editable: owned cars/tracks (as `Set` of indices into
  `DATA.cars`/`DATA.tracks`), favourite series, active filters, current date, and per-series
  "phase" overrides (see cadence parsing below).
- **Series identity across season swaps**: iRacing's series name embeds the season instance (e.g.
  "iRacing Porsche Cup by CONSPIT - 2026 Season 3"). `seriesKey()` strips that suffix so
  favourites/phase overrides survive loading a new `season.json` each season; `seriesLabel()` is
  the display variant (also drops a trailing "Fixed", shown separately as a badge).
  `findSeries(name)` re-resolves a stored favourite to the current season's series index.
- **Race-time cadence parsing** (`parseCadence`/`nextStart`): iRacing publishes race cadence as
  free text ("Every 2 hours", "Thur & Sat at 10, 19 GMT", etc.), always in GMT. This is parsed into
  a small spec and used to compute "next race" countdowns per row, re-ticked every second
  (`tickNext`). Some "every 2 hours" series don't state whether they land on odd/even GMT hours —
  the UI shows `~` and lets the user pin a guess (`state.phase`), persisted per `seriesKey`.
- **Persistence has three backends**, tried in this order at boot (`chooseBackend`):
  1. `folder` — `planner-settings.json` via `serve.py`'s `/planner-api`.
  2. `browser` — `localStorage`, when the page has a real origin.
  3. `none` — nothing writable; "Save a copy" (which bakes current state into a new downloadable
     HTML file) becomes the only way to persist.

  Saves are debounced (1.2s) and skipped if nothing changed, since every write costs an upload in a
  synced folder. Never assume `localStorage`/`fetch` succeed — every backend method is defensive
  about its own failure and falls back gracefully.
- **Boot order** (bottom of the script): pick a backend → check for a `season.json` sitting next
  to the page (always wins if present and parses) → fall back to `window.__SEASON__` (baked into a
  "Save a copy" export) → fall back to the embedded empty schedule, which triggers `noSchedule()`
  (a "you need to generate season.json" screen). Loading a season preserves owned
  cars/tracks/favourites by name (see above), not by index.
- **"Save a copy"** (`buildCopy`/`saveCopy`) clones the live DOM, strips generated content, and
  injects the current `DATA` + `snapshot()` as inline `<script>` globals (`window.__SEASON__` /
  `window.__SAVED__`) so the downloaded HTML file is fully self-contained and opens with the same
  state — this is the offline/no-backend save path.
- Encoding care throughout: `decodeJSONBytes()` sniffs BOM/UTF-16 in `season.json` because
  PowerShell's `>` redirect and some editors don't write plain UTF-8, and a silent parse failure
  there would otherwise look like "no schedule."

## Editing conventions already in the code

- No inline comments explaining *what* code does; comments here are reserved for *why* (e.g. the
  UTF-16 gotcha, the sticky-header/overflow interaction note in the CSS, the season-key stripping
  rationale). Keep matching that style.
- `$` is a shorthand for `document.querySelector`, defined once near the top of the script.
- Bump the `BUILD` constant (`'YYYY-MM-DD.N'`, near the top of the `<script>`) when shipping a
  user-visible change to `index.html` — it's surfaced in the UI footer and the backup modal so
  users can tell which version they're on.
