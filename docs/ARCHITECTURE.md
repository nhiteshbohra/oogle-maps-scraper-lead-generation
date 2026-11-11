# Architecture

## System Flow

```
Browser Page  <-- HTTP, localhost:8090 -->  analyze.py  -- starts/stops -->  google-maps-scraper.exe
                                                |                                     |
                                                |  reads appended bytes only          |  appends rows
                                                +------------  data/leads.csv  <------+
                                                |
                                                +--  leads.json (notes)   edits.json (corrections)
```

1. **User interaction**: The user enters an area and chooses business types in the browser dashboard and clicks **Find businesses** (`POST /scrape/start`).
2. **Scraper Orchestration**: `ScrapeManager` (`scraper_control.py`) configures the keyword list and launches the scraper executable in grid search mode. The scraper divides the target geographical area into coordinate grid squares and queries each business type per square.
3. **Data Ingestion**: The scraper appends discovered places to `data/leads.csv` and updates completed search cells in `leads.csv.resume.json`.
4. **Live Polling**: Every 10 seconds, the frontend dashboard polls `/data.json?epoch=E&have=N`. `PlaceStore` performs append-only streaming, reading only the newly added bytes since the previous poll and sending back new rows.
5. **Client-side Processing**: The browser client dynamically filters, calculates digital service lead scores, and renders the interactive data table, analytics charts, and maps.

## Component Breakdown

| Component | Location | Responsibility |
|---|---|---|
| `PlaceStore` | `analyze.py` | Incremental CSV parsing with byte-offset tracking, CID de-duplication, record formatting, business classification, and edit overlays. |
| `EditBook` / `LeadBook` | `analyze.py` | Persists user annotations, corrections, outreach statuses, and notes in isolated JSON files. |
| `Dashboard` | `analyze.py` | Local HTTP API server handling `/data.json`, `/place.json`, `/leads.json`, `/scrape.json`, and mutation endpoints (`POST /lead`, `/edit`, `/add`, `/scrape/*`). |
| Frontend UI | `web/` (`index.html`, `style.css`, `app.js`) | Responsive single-page application (SPA) with zero external build dependencies. Uses Leaflet.js for interactive geospatial mapping, client-side pagination, quick copy, and keyword management. |
| `ScrapeManager` | `scraper_control.py` | Process supervisor for the scraper executable: handles resume loops, detects throttling or network blocks, and keeps the OS awake. |
| Scraper Engine | Go (`main.go`, `gmaps/`, `runner/`, `grid/`) | High-concurrency headless Chromium scraper built on top of `playwright-go` and a patched `scrapemate`. |

## Key Architectural Principles

- **Immutability of Scraped Data**: Scraped CSV data is never modified by the dashboard. User corrections and notes are kept in separate `.edits.json` and `.leads.json` files and layered on top dynamically in memory.
- **Append-Only Streaming**: The CSV file can grow significantly during large scraping campaigns. Rather than re-reading the entire file on each poll, `PlaceStore` tracks byte offsets, avoiding I/O bottlenecks.
- **Compact Data Transfer**: Places are transmitted to the frontend as compact positional arrays rather than keyed objects, reducing payload size by ~70% and accelerating browser rendering.
- **Supervisor-Worker Loop**: Rather than a fragile single long-running process, the scraper operates under a supervisor loop that cleanly handles restarts with `-resume` to gracefully survive rate-limits.
- **Zero-Install Client Experience**: The Python dashboard uses Python standard library only (`http.server`, `urllib`, `json`, `csv`, `sqlite3`). No `pip install` or external Python dependencies required.
- **Local-First Security**: The dashboard binds strictly to `127.0.0.1`. No external cloud transmission of scraped leads or phone numbers occurs.

