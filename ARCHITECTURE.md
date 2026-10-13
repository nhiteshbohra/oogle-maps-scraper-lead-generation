# Architecture

## Flow

```
Browser page  <-- HTTP, localhost:8090 -->  analyze.py  -- starts/stops -->  google-maps-scraper.exe
                                               |                                     |
                                               |  reads only appended bytes          |  appends rows
                                               +------------  data/leads.csv  <------+
                                               |
                                               +--  leads.json (notes)   edits.json (corrections)
```

1. The user presses **Find businesses**. The page posts area + business types to `/scrape/start`.
2. `ScrapeManager` (`scraper_control.py`) writes the keyword file and runs the exe in grid mode.
   The exe cuts the area into squares and searches every business type in every square.
3. The exe appends each place to `data/leads.csv` and records finished searches in
   `leads.csv.resume.json`.
4. Every 10 s the page asks `/data.json?epoch=E&have=N`. `PlaceStore` reads only the bytes added
   since last time and returns only rows the page does not have.
5. The page filters, scores and draws everything client-side from those rows.

## Components

| Component | Where | Responsibility |
|---|---|---|
| `PlaceStore` | `analyze.py` | Incremental CSV reader, de-duplication by Google cid, row building, classification, applying edits. |
| `EditBook` / `LeadBook` | `analyze.py` | The user's corrections and lead notes, each in its own JSON file. |
| `Dashboard` | `analyze.py` | HTTP endpoints: `/data.json`, `/place.json`, `/leads.json`, `/scrape.json`, `POST /lead /edit /add /scrape/*`. |
| Page | `REPORT_TEMPLATE` in `analyze.py` | All UI. Holds every row in memory; detail data is fetched per place. |
| `ScrapeManager` | `scraper_control.py` | Runs the exe, restarts it with `-resume`, decides finished / blocked, keeps the PC awake. |
| Scraper | Go code | Google Maps scraping (grid search, place pages, CSV writer, resume state). |

## Why it is built this way

- **Scraper data is never modified.** Edits and notes are layered on top at read time, so a
  rescrape or a bug in the app cannot destroy the user's work or the raw data.
- **Append-only reading.** The CSV grows to tens of MB while scraping; re-parsing it every 10 s
  made the page lag. The store keeps a byte offset per file. If a file shrinks it reloads once
  and bumps `epoch` so pages reload too.
- **Rows are positional lists, not objects.** 10k+ rows are sent as JSON and filtered on every
  keystroke; lists are several times smaller and faster.
- **Bulky data is separate.** Reviews, hours and photos are only sent when a place is opened.
- **A supervisor loop, not one long run.** The exe exits on Google throttling or inactivity.
  Restarting with `-resume` skips finished searches, so the loop converges. A pass that runs zero
  jobs means everything is done.
- **Standard library only.** The app must start with a double-click on a customer PC.

## Constraints

- Google shows about 120 results per search. Coverage comes from small squares x many business
  types, not from scrolling deeper.
- Speed is bounded by Google throttling and PC resources: about 25 searches/minute at 8 parallel
  browsers on the development machine.
- Locality and category are guessed when Google gives none; guesses are flagged in the row
  (`row[27]`) and explained in the detail panel.
- Windows only: launcher, `taskkill`, keep-awake call and the exe.
- The server has no login. It listens on `127.0.0.1` only and must stay that way.
- Lead score assumes the seller offers websites / WhatsApp services (no website = good lead).
