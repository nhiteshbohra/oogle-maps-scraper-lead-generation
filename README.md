# Lead Finder

Find local businesses on Google Maps and work them as sales leads, from one page in your browser.

Built for selling digital services (websites, WhatsApp automation, review management) to local
shops and service businesses: it collects every business in an area, scores how promising each
one is, and lets you track who you called and what they said.

## Start

1. Double-click **`Start Lead Finder.bat`**.
2. Your browser opens the app. Keep the black window open; closing it stops the app.
3. Press **Find businesses**, type a city, press **Find on map**, then **Start search**.

Leads appear on their own while the search runs. You can stop any time; next time it continues
where it left off.

Needs Windows, [Python 3.10+](https://www.python.org/downloads/) and
[Node.js](https://nodejs.org/) on the PATH.

## What you can do

| | |
|---|---|
| **Find businesses** | Pick an area and business types. Runs until every square of the area is searched. |
| **Filter** | Search, business type, area, phone, lead score, lead status. **More filters** adds website, rating, opening hours, review activity, unhappy customers and more. |
| **Lead score** | 0-100 per business: mobile number, no website, unclaimed listing, reviews, rating. Click a business to see why. |
| **Details** | Click a row: photo, call / WhatsApp buttons, address, hours, star breakdown, recent reviews. |
| **Track** | Mark a lead Called / Interested / Not interested / Customer and keep notes. |
| **Correct** | **Edit details** to add a phone number you found yourself. **+ Add business** for one not on Google Maps. |
| **Insights** | **Show insights** for charts, a per-category table and a map. |
| **Export** | **Export filtered CSV** saves the list you are looking at, ready for Excel. |

## Your data

Everything stays on your PC in the `data` folder:

| File | Holds |
|---|---|
| `leads.csv` | What was scraped from Google Maps. Never edited by the app. |
| `leads.csv.leads.json` | Your lead statuses and notes. |
| `leads.csv.edits.json` | Your corrections and added businesses. |
| `leads.csv.scrape.json` | The area and business types of your search. |
| `leads.csv.resume.json` | Which searches are finished, so a restart skips them. |

Back up the `data` folder to keep your work.

## Use it responsibly

Scraped phone numbers belong to real people and businesses. Unsolicited bulk WhatsApp messages
get the sending number banned and may break local rules on commercial messaging (TRAI in India).
Call first or message in small, personal batches.

## For developers

See [CLAUDE.md](CLAUDE.md) for the project map and [ARCHITECTURE.md](ARCHITECTURE.md) for how the
parts fit together. To rebuild the scraper: `make build` (Go 1.27+).

## Credits

The scraper engine is a trimmed copy of
[gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper) and
[gosom/scrapemate](https://github.com/gosom/scrapemate) by Georgios Komninos, used under the MIT
license. See [LICENSE](LICENSE) and `third_party/scrapemate/LICENSE`.
