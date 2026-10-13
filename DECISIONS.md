# Decision log

Append-only. If a decision is reversed, add a new entry saying so; do not delete the old one.

## 2026-10-12 - Patch scrapemate instead of running in WSL/Docker
- **Decision**: keep a patched copy of scrapemate without the Chromium flags `--single-process`
  and `--no-zygote`.
- **Alternatives**: run upstream in WSL or Docker as its README suggests.
- **Why**: those flags crash Chrome on Windows (exit 3221226505, surfaced as "unexpected page
  type"). The user wants a native double-click app; WSL/Docker is not acceptable for customers.

## 2026-10-12 - Build the exe as 64-bit
- **Decision**: always `GOARCH=amd64`.
- **Why**: the installed Go is a 32-bit build; a 32-bit exe cannot get a bundled Node for the
  browser driver and is limited to ~2-4 GB memory.

## 2026-10-13 - Grid search by business type instead of city-wide queries
- **Decision**: search every business type in every 2 km square of the area.
- **Alternatives**: one "X in Jodhpur" query per type (first attempt).
- **Why**: Google caps a search at ~120 results. One 1 km square with one keyword returned 8
  places missing from the whole city-wide run.

## 2026-10-13 - Supervisor restarts the scraper until done
- **Decision**: loop the scraper with `-resume`; finished = a pass that runs zero jobs.
- **Why**: the scraper exits on throttling/inactivity and a first run died overnight unnoticed.
  The scraper's own cell-count estimate is too rough to detect completion.

## 2026-10-13 - Live local web app, not a generated HTML report
- **Decision**: `analyze.py` serves a page that polls for new rows.
- **Alternatives**: static HTML report (first version, removed).
- **Why**: the user wants to watch leads arrive while scraping.

## 2026-10-13 - Read the CSV append-only; send only new rows
- **Why**: re-reading the whole file on every poll does not scale and the user asked for it.

## 2026-10-13 - Lead score assumes the seller offers digital services
- **Decision**: mobile +30, no website +25, unclaimed +15, reviews up to +15, rating 4+ +10,
  recent review +5; closed = 0.
- **Why**: the user sells websites and WhatsApp automation. Other offers need other weights.

## 2026-10-13 - Scroll continues when results load, not after a fixed sleep
- **Decision**: changed `scroll()` in `gmaps/job.go` to poll for feed growth.
- **Why**: every search took a fixed 33 s. Now ~13 s with the same results (A/B tested).

## 2026-10-13 - Keep scroll depth 10; do not lower to 5
- **Alternatives**: depth 5 for more speed (the user asked for it).
- **Why**: measured: only ~25% faster but 27% fewer places inside a dense square.

## 2026-10-13 - Hide low-fit businesses by default instead of not scraping them
- **Decision**: vegetable/meat/dairy/tea/paan/liquor shops, temples, offices etc. are flagged
  `row[24] = 0` and hidden by a filter; their keywords were removed from `keywords.txt`.
- **Why**: they rarely buy digital services, but other keywords still return them.

## 2026-10-13 - Guess missing area and type, and say so
- **Decision**: area from Google, else address, else neighbours (3 of 5 within 600 m), else
  pincode; type from the business name. Guesses are flagged and explained.
- **Why**: 7% of places had no area and 100 no type; the user asked how to justify them.

## 2026-10-13 - User edits live in a separate file
- **Decision**: corrections in `.edits.json`, applied over the scraped row at read time.
- **Alternatives**: rewrite `leads.csv`.
- **Why**: the scraper appends to that file and a rescrape must not undo the user's work.

## 2026-10-14 - Trim the upstream repo in place; fresh git history
- **Decision**: delete unused upstream parts (web UI, SaaS, admin, AWS Lambda, Postgres, Docker,
  docs, ads) and start a new git history. Old history kept as a backup folder outside the repo.
- **Alternatives**: new clean folder with only our files (recommended at the time); keep everything.
- **Why**: owner's choice. Go files went from ~240 to 43; build and vet still pass.

## 2026-10-14 - Scraping is controlled from the page
- **Decision**: `ScrapeManager` in Python replaces `run_scrape.ps1`; area found by name through
  OpenStreetMap Nominatim from the browser.
- **Why**: a customer must not need PowerShell.

## 2026-10-14 - Simple view by default
- **Decision**: six filters, four quick views, four tiles and the table by default; the rest
  behind **More filters** and **Show insights**. Nothing was removed.
- **Alternatives**: delete advanced features.
- **Why**: owner's choice: simple for customers without losing analysis.

## 2026-10-14 - Scraper telemetry off
- **Decision**: `ScrapeManager` sets `DISABLE_TELEMETRY=1`.
- **Why**: the upstream scraper reports usage to its author; a customer product should not.
