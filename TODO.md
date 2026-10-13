# To do and known issues

## Open

- [ ] Continue the Jodhpur search (paused at 2,606 searches, ~21,000 planned).
- [ ] Double-click test of `Start Lead Finder.bat` on a second PC without the dev setup.
- [ ] Hide / delete a business from the list (asked about, not built).
- [ ] Confirm Claude Code's co-author setting is off for this repo so future commits stay clean
      (see `docs/git-claude-attribution-audit.md`, step 6).
- [ ] Ship the exe to customers: it is git-ignored, so a release zip or build step is needed.
- [ ] More data per place, needs scraper work: owner replies to reviews, booking link, social links.

## Known issues

- **`go test ./runner/filerunner` fails on Windows** with "TempDir RemoveAll cleanup ... being used
  by another process". Upstream tests leave the input file open; Linux is unaffected. Not caused
  by our changes. Left as is.
- **Search total is "about"**: the scraper's square count is an estimate and can overshoot; the
  bar shows the real number once the search finishes.
- **"Reviewed in last 3 months" undercounts**: only 8 reviews per place are saved and Google
  picks "most relevant", not newest.
- **Some photos are gone**: expired Google image links are hidden silently.
- **Neighbour-guessed areas can differ slightly between runs**: places that arrive live are
  voted on with the data present at that moment.
- **Old lead data**: `jodhpur.csv` / `jodhpur_clean.csv` (first city-wide run, 1,360 places) were
  deleted by the owner and may still be in the Recycle Bin.

## Intentionally left as is

- Kirana / general / grocery stores stay in the list (biggest group; owner has not decided).
- Scroll depth stays 10 (see `DECISIONS.md`).
- `s3uploader/`, `leadsdb/`, `tlmt/` and unused flags remain in the Go code: `runner` imports
  them and removing them means editing upstream logic for no user benefit.
- No login on the page: it only listens on this PC.
- Busy hours and review trend are Google estimates / samples, not real footfall.
