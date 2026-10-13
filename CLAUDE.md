# Lead Finder - project map and rules

Read this, `ARCHITECTURE.md`, `DECISIONS.md`, `SESSION_LOG.md` and `TODO.md` before changing code.

## Purpose

Collect every business in an area from Google Maps and work them as sales leads for digital
services (websites, WhatsApp automation). One local web page: start a search, filter and score
leads, track calls, correct data, export.

## Tech stack

- **App**: Python 3.12, standard library only (no pip installs). Page is plain HTML/CSS/JS inside
  `analyze.py`; Leaflet 1.9.4 from unpkg for maps.
- **Scraper**: Go 1.27, built to `google-maps-scraper.exe` (64-bit Windows). Drives headless
  Chromium through playwright-go, which needs Node.js on the machine.
- **Storage**: flat files in `data/`. No database.

## Layout

| Path | What it is |
|---|---|
| `Start Lead Finder.bat` | Double-click launcher: runs `analyze.py`. |
| `analyze.py` | The app: CSV reader, lead scoring, classification, HTTP server, and the whole page (`REPORT_TEMPLATE`). |
| `scraper_control.py` | `ScrapeManager`: runs the scraper exe, restarts it with `-resume` until done. |
| `keywords.txt` | Recommended business types, most valuable first. |
| `main.go`, `runner/`, `gmaps/`, `grid/`, `deduper/`, `exiter/`, `leadsdb/`, `s3uploader/`, `tlmt/`, `internal/` | Scraper engine (trimmed upstream code). |
| `third_party/scrapemate/` | Patched copy of the scraping library; `go.mod` points at it. |
| `data/` | Leads and the user's work. Git-ignored. |
| `archive/` | Superseded scripts and keyword lists. Git-ignored. |
| `docs/` | Process prompts this project follows. |

## Run, build, test

- Run the app: `python analyze.py` (or the `.bat`). Opens `http://localhost:8090`.
- Export without the UI: `python analyze.py --export`.
- Build the scraper: `make build`, or `GOARCH=amd64 go build -o google-maps-scraper.exe .`
- Go checks: `go vet ./...`, `go test ./...` (see `TODO.md` for the known Windows test failures).
- There is no Python test suite in the repo; UI changes are checked by driving the page in
  headless Chromium (see `SESSION_LOG.md`).

## Conventions

- Python: standard library only, 4 spaces, named constants instead of magic numbers, comments
  say why not what.
- A place is a positional list ("row"). The layout is documented at the top of the page script in
  `analyze.py` and must stay identical in Python and JS. New fields go at the end.
- User-facing text is plain English for a non-technical user: no file names, no jargon.
- Go: `gofmt` (tabs), follow `AGENTS.md`.

## Do NOT

- Do not write to `data/leads.csv` from the app. Corrections go to `.edits.json`, notes to `.leads.json`.
- Do not add Python dependencies or a database without asking.
- Do not put back the Chromium flags `--single-process` / `--no-zygote` in
  `third_party/scrapemate`: they crash Chrome on Windows.
- Do not build the exe as 32-bit; the browser driver has no 32-bit Node.
- Do not commit `data/`, `archive/` or `*.exe`: leads contain phone numbers.
- Do not bind the server to anything but `127.0.0.1`.
- Do not add Claude/Anthropic co-author lines to commits. Never push without the owner's say.
