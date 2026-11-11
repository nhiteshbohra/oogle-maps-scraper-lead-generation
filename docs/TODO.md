# Roadmap, Tasks & Known Issues

## Open Tasks & Enhancements

- [x] Validate `make_release.py --version` parameter to reject path traversal components.
- [x] Clean up and remove obsolete historical experimental scripts (`archive/`).
- [x] Organize project documentation into dedicated `docs/` hierarchy (`docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/TODO.md`).
- [x] Interactive UI pagination suite (dynamic per-page selector, smart numbered pills, dual top/bottom controls, quick jump).
- [x] In-table lead qualification (inline status selector, 1-click phone/summary copy, WhatsApp direct links).
- [x] Keyboard navigation shortcuts (arrow keys for pagination, '/' to search, 'Esc' to dismiss).
- [x] UI keyword pack manager (embedded category packs & custom business type additions directly in UI without disk files).
- [x] Frontend modularization (decouple embedded HTML/CSS/JS template into modular `web/` assets: `index.html`, `style.css`, `app.js`).
- [ ] Add option in dashboard UI to hide or delete individual businesses from active view.
- [ ] Code-signing certificate for Windows releases to prevent SmartScreen untrusted binary warnings on fresh PCs.
- [ ] Extract additional place metadata during scraping: owner review responses, booking links, and linked social profiles.
- [ ] Test self-contained release ZIP packaging on independent clean Windows test environments.

## Known Technical Considerations

- **Platform Target**: The integrated supervisor, process job object management, and launcher scripts are optimized for Windows 64-bit (`amd64`).
- **Playwright Test Scaffolding on Windows**: Upstream `third_party/scrapemate` unit tests utilize POSIX signals (`syscall.Kill`) which are not supported on Windows. The core scraping engine compiles and executes cleanly.
- **Estimated Result Volumes**: Scraper coordinate square approximations provide approximate total estimates; the progress bar updates to exact counts as cells finalize.
- **Review Sampling Window**: Google Maps limits standard place review payloads to the top 8 "most relevant" reviews rather than chronological sorting.
- **Image URL Expiry**: Google temporary image CDN URLs may expire over extended periods; broken images are hidden gracefully in the dashboard place inspector.
- **Locality Consensus**: Area labels inferred via spatial nearest neighbors depend on already-discovered adjacent entries and refine progressively as more places in an area are scraped.

## Intentional Architecture Invariants

- **Standard Library UI**: `analyze.py` strictly relies on the Python 3 standard library (`http.server`, `urllib`, `sqlite3`, `csv`, `json`) to guarantee zero-dependency portable deployment.
- **Local-Only Binding**: Web server strictly binds to `127.0.0.1` (`localhost:8090`) to prevent accidental external network exposure.
- **Scroll Depth**: Default scroll depth is maintained at 10 to balance scraping speed against result completeness in dense urban zones.

