# Session log

Newest first. One entry per work session: what changed, why, what is half-done.

## 2026-10-14 - Turned the experiment into a project
- Trimmed upstream scraper: removed web UI, SaaS, admin, AWS Lambda, Postgres, Docker, docs and
  ads. `main.go` keeps file mode and Playwright install only. `go build` and `go vet` pass.
- Moved the patched scrapemate into `third_party/scrapemate` so the repo builds on its own.
- Added `scraper_control.py` and a **Find businesses** panel: start/stop/continue a search from
  the page. Replaces `run_scrape.ps1` (now in `archive/`).
- Simple view by default; **More filters** and **Show insights** reveal the rest.
- Added `Start Lead Finder.bat`. Default data file is now `data/leads.csv` (was
  `jodhpur_grid.csv`); the Jodhpur run's files were moved there with its resume state.
- New README; added the five context files; fresh git history.
- **Checked**: headless-browser run of filters, presets, detail panel, notes, edit, restore,
  insights, and a real one-square search started from the page that finished by itself.
- **Not checked**: the `.bat` by double-click; a full city search started from the page.
- **Half-done**: Jodhpur search is paused at 2,606 searches; press **Continue search** to go on.

## 2026-10-13 - Coverage, speed and lead tools
- Grid search with a supervisor script; keyword lists widened to all business types, then
  narrowed and reordered for digital-services leads (gym first).
- Scroll fix in `gmaps/job.go` and 8 parallel browsers: 7 -> ~25-30 searches/minute.
- Dashboard: live append-only updates, lead score, detail panel, lead notes, hours / activity /
  unhappy-customer filters, busy-hours chart, area and type guessing, editing and manual adds.
- UI lag fixed: locality list 1,770 -> ~120 options, map drawn after the table.

## 2026-10-12 - First run on Windows
- Exe was 32-bit and Chrome crashed in single-process mode: rebuilt 64-bit against a patched
  scrapemate. First city-wide scrape of Jodhpur (1,360 places) and first static report.
