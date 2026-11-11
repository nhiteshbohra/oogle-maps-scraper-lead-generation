# Architecture Decision Records (ADRs)

Append-only record of design decisions. If a decision is reversed, a new entry is added explaining the context and rationale.

---

## ADR-001 (2026-10-12): Patch `scrapemate` instead of running in WSL/Docker
- **Decision**: Maintain a patched copy of `scrapemate` without the Chromium flags `--single-process` and `--no-zygote`.
- **Alternatives**: Run upstream in WSL or Docker as upstream README suggests.
- **Why**: Those flags crash Chrome on Windows (exit code `3221226505`, surfaced as "unexpected page type"). The goal is a native double-click desktop app on Windows without requiring WSL/Docker.

## ADR-002 (2026-10-12): Build executable as 64-bit AMD64
- **Decision**: Always build with `GOARCH=amd64`.
- **Why**: 32-bit executables cannot bundle Node.js for Playwright-Go and run into 2-4 GB memory limitations during large browser scraping runs.

## ADR-003 (2026-10-13): Grid search by business type instead of city-wide queries
- **Decision**: Divide the target geographic bounding box into ~2 km coordinate squares and search each business type per square.
- **Alternatives**: Single query per type for the whole city (e.g., "X in Jodhpur").
- **Why**: Google Maps caps search results at ~120 places per query. Grid search prevents truncated results in high-density areas.

## ADR-004 (2026-10-13): Supervisor restart loop with resume state
- **Decision**: Loop the scraper executable with `-resume`. Completion is reached when a full pass executes zero jobs.
- **Why**: Headless browsers and network requests face intermittent throttling and inactivity drops. The checkpoint resume state allows the supervisor to converge to 100% completion reliably.

## ADR-005 (2026-10-13): Real-time local web application over static reports
- **Decision**: Serve an interactive web UI via `analyze.py` that polls for incoming records in real time.
- **Alternatives**: Generate a static batch HTML report post-run.
- **Why**: Users can inspect, qualify, and triage leads immediately while scraping continues in the background.

## ADR-006 (2026-10-13): Append-only CSV ingestion
- **Decision**: `PlaceStore` tracks byte offsets into `data/leads.csv` and reads only appended bytes on every poll.
- **Why**: Re-reading tens of megabytes every 10 seconds causes CPU spikes and frontend lag.

## ADR-007 (2026-10-13): Digital service outreach lead scoring
- **Decision**: Prioritize leads based on digital service opportunity: mobile phone present (+30), missing website (+25), unclaimed listing (+15), high review volume (+15), rating 4+ (+10), recent review activity (+5); permanently closed = 0.
- **Why**: Targets local businesses primed for website creation and WhatsApp marketing automation.

## ADR-008 (2026-10-13): Event-driven scroll polling
- **Decision**: Update `scroll()` in `gmaps/job.go` to poll for feed element additions rather than static sleeping.
- **Why**: Reduces per-search duration from ~33s down to ~13s while retrieving identical result counts.

## ADR-009 (2026-10-13): Maintain scroll depth of 10
- **Decision**: Keep scroll depth at 10 rather than reducing to 5.
- **Why**: Benchmarked: depth 5 was only ~25% faster but missed 27% of businesses in dense areas.

## ADR-010 (2026-10-13): Filter low-fit businesses client-side
- **Decision**: Categorize low-fit businesses (e.g. tobacco, paan, meat shops, shrines) with `row[24] = 0` and hide them by default via UI filters rather than filtering at the scraper stage.
- **Why**: Google searches for adjacent terms often return these establishments; client filtering keeps the scraper unbiased.

## ADR-011 (2026-10-13): Fallback area and category inference
- **Decision**: Infer area using Google locality, street address parsing, spatial nearest-neighbor voting (3 of 5 within 600m), or pincode. Infer business type from company title when missing.
- **Why**: ~7% of listings lack explicit area metadata; inferences are clearly tagged (`row[27]`) in the UI.

## ADR-012 (2026-10-13): Store user corrections in sidecar files
- **Decision**: User edits and notes are stored in `.edits.json` and `.leads.json`, layered dynamically over the CSV in memory.
- **Why**: Preserves data provenance; rescraping or data refreshes will never overwrite user-entered notes or call statuses.

## ADR-013 (2026-10-14): Trim unused upstream features
- **Decision**: Remove telemetry, AWS S3 uploaders, Postgres DB layers, SaaS admin panels, and unneeded dependencies.
- **Why**: Reduced Go codebase footprint from ~240 files to core essentials; smaller binary size, faster compile times, and complete zero-telemetry privacy.

## ADR-014 (2026-10-14): Embedded browser-based control
- **Decision**: The browser UI directly manages scraping sessions (`ScrapeManager`), utilizing OpenStreetMap Nominatim for automated geocoding.
- **Why**: Eliminates manual terminal / PowerShell commands for everyday users.

## ADR-015 (2026-10-14): Self-contained release distribution
- **Decision**: `make_release.py` bundles the app, stripped scraper executable (`-trimpath -ldflags "-s -w"`), and official embeddable Python runtime into a single ZIP.
- **Why**: End-users can double-click and run on any modern 64-bit Windows machine without configuring Python or Go environments.

## ADR-016 (2026-10-14): Windows Job Object process confinement
- **Decision**: Encapsulate the scraper executable inside a Windows Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`).
- **Why**: Ensures that closing the dashboard command window automatically terminates child scraper processes, preventing orphaned browser instances.

## ADR-017 (2026-11-11): Client-side smart pagination and in-table outreach actions
- **Decision**: Implement dynamic dual-placement pagination with configurable page sizing (`25`, `50`, `100`, `250`, `All`), numbered smart pills, and inline lead status modification directly on table rows.
- **Why**: Allows users managing large datasets (10,000+ rows) to browse smoothly without DOM lag, navigate immediately at either end of the table, and qualify prospects without having to repeatedly open and close the detail drawer.

## ADR-018 (2026-11-11): Embed curated keyword packs into application code
- **Decision**: Embed the default 200+ business keyword list and category packs directly inside application code and expose full keyword management (custom additions, quick category packs, clear) in the browser UI, removing the standalone `keywords.txt` file from the repository root.
- **Why**: Allows the user to configure and operate the entire application directly from the web interface without having to touch, edit, or maintain external text files on disk.

## ADR-019 (2026-11-11): Extract frontend into dedicated `web/` assets
- **Decision**: Extract the monolithic 1,300+ line embedded HTML/CSS/JS template (`REPORT_TEMPLATE`) from `analyze.py` into dedicated static assets in `web/` (`index.html`, `style.css`, `app.js`) served directly by the Python HTTP handler.
- **Why**: Drastically improves codebase modularity and developer ergonomics (proper HTML/CSS/JS syntax highlighting, linting, formatting), slashes `analyze.py` file size by over 55%, and decouples frontend UI enhancements from backend Python logic without requiring any external build tooling.

## ADR-020 (2026-11-11): Dedicated full-page Campaign Manager & Target Keyword Studio
- **Decision**: Introduce a top-level tabbed navigation separating "🎯 Target & Scrape" (Campaign Manager) from "📋 Leads & CRM". Replace the slide-out drawer with a dedicated full-screen workspace featuring interactive keyword chips, category pack quick-selection, bulk keyword pasting, interactive geocoded area mapping, live search estimates, and real-time execution controls.
- **Why**: Dramatically improves onboarding for new users starting from scratch with 0 leads. Empowers users to configure, manage, and monitor their targeted business campaigns entirely from the browser without ever needing terminal commands or manual configuration files.

## ADR-021 (2026-11-11): In-browser lead exclusion, database reset with auto-backup, and live log streaming
- **Decision**: Add full lead exclusion/hiding (tracked in `.edits.json` and filtered client-side with restoration support), safe database reset with automatic timestamped directory backups, and real-time scraper log streaming via `/scrape.json` inside the web interface.
- **Why**: Eliminates the remaining manual tasks that previously required opening Windows File Explorer or checking terminal windows, allowing the user to manage the complete lead lifecycle directly through the browser.

