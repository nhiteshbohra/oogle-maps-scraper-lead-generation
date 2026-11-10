# Lead Finder

Find businesses on Google Maps, filter potential leads, and keep track of your outreach in a local browser dashboard.

Lead Finder is built for people offering websites, WhatsApp automation, and other digital services to local businesses. Choose an area and business types, then review results as they arrive.

## Installation guide

### Option 1: Install from a release ZIP

**Requirements:** 64-bit Windows, a web browser, and an internet connection.

1. Open this repository's **Releases** page and download the Lead Finder ZIP, if a release is available.
2. Right-click the ZIP, choose **Extract All**, and extract it into a folder where you can save files. Open the extracted folder; do not run the app from inside the ZIP.
3. Double-click **Start Lead Finder.bat**.
4. Your browser opens the dashboard at `http://localhost:8090`.

The release includes Python. The first search downloads the browser components it needs, which can take a few minutes and several hundred MB. Keep the application window open while using Lead Finder; closing it stops the app and its scraper.

There is no separate installer. You do not need to install Python or Go when using the release ZIP.

If no release has been published yet, use [Option 2: Install from source](#option-2-install-from-source). GitHub's **Download ZIP** contains source code and does not include the bundled runtime or scraper executable.

### Option 2: Install from source

1. Install Python 3.10 or newer from [python.org](https://www.python.org/downloads/windows/). Enable **Add Python to PATH** in the installer.
2. Install Go 1.27.1 or newer using the Windows AMD64 installer from [go.dev](https://go.dev/dl/).
3. On this repository's GitHub page, choose **Code → Download ZIP**, then extract it. Keep the entire folder, including `third_party`.
4. Open the extracted project folder in File Explorer. Type `powershell` in its address bar and press Enter.
5. Check that both tools are available:

```powershell
python --version
go version
```

If either command is not found, reopen PowerShell after installation and check that the tool was added to PATH. The Python app uses the standard library and needs no pip packages.

Build the scraper and start the app from that same PowerShell window:

```powershell
$env:GOOS = "windows"
$env:GOARCH = "amd64"
go build -trimpath -ldflags "-s -w" -o google-maps-scraper.exe .
python analyze.py
```

The first build downloads Go dependencies, so it needs an internet connection. When the app starts, open `http://localhost:8090` if the browser does not open automatically.

After building the scraper, you can start the app on later visits by double-clicking **Start Lead Finder.bat**. Keep `google-maps-scraper.exe` beside `analyze.py`.

### Build a release ZIP

To create a release ZIP with bundled Python:

```powershell
python make_release.py --version 1.0
```

The ZIP is written to `dist/`. Build output and personal lead data are excluded from Git.

## Find businesses

1. Click **Find businesses**.
2. Enter a city or area and click **Find on map**.
3. Choose business types or use the recommended list.
4. Select the search detail and speed, then click **Start search**.

Results appear while the search runs. Smaller search squares take longer but can find more businesses. You can stop a search and continue it later with its saved progress.

Google can limit results or throttle searches, so coverage and completion times vary. A completed search does not guarantee that every business in an area was found.

## Work with your leads

| Feature | What it does |
|---|---|
| Filters | Narrow results by business type, area, phone number, lead score, and lead status. Open **More filters** for additional options. |
| Lead score | Prioritize businesses using signals such as phone availability, website presence, and reviews. |
| Business details | View available contact information, opening hours, ratings, photos, and sampled reviews. |
| Outreach tracking | Record notes and mark leads as Called, Interested, Not interested, or Customer. |
| Corrections | Edit business details or add a business manually. |
| Insights | Explore charts, category summaries, and a map through **Show insights**. |
| CSV export | Download the filtered list for use in a spreadsheet. |

Some details may be missing or outdated. Lead scores help prioritize outreach; they do not predict whether a business will buy your services.

## Save, back up, and update

Your leads, notes, corrections, and search progress are saved in the `data` folder beside the app. Corrections and notes are stored separately from the scraped results.

To back up your work, close Lead Finder and copy the entire `data` folder somewhere safe.

To update, extract the new release into a separate folder, then copy your backed-up `data` folder into it before starting the new version. Keep the old copy until you have confirmed your data appears correctly.

## Troubleshooting

- **The browser does not open:** open `http://localhost:8090` manually while the application window is running.
- **The first search takes time to start:** allow the browser download to finish and check your internet connection.
- **The PC becomes slow:** choose the Gentle speed setting.
- **Results stop arriving:** check the search status. Google may be throttling requests; allow time before continuing.
- **Python or the scraper is missing:** use the full release ZIP, or complete the source setup below.

## Documentation

Detailed architectural notes, design decisions, and roadmap can be found in the [`docs/`](docs/) directory:
- [**System Architecture**](docs/ARCHITECTURE.md) - End-to-end data flow, components, and supervisor loop.
- [**Architecture Decision Records (ADRs)**](docs/DECISIONS.md) - History of design choices and technical trade-offs.
- [**Roadmap & Known Issues**](docs/TODO.md) - Planned features and platform details.
- [**Agent Guidelines**](AGENTS.md) - Development commands, test suites, and Go style guide.

## License and credits

Released under the [MIT License](LICENSE). The scraper is based on
[gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper) and includes a patched copy of
[gosom/scrapemate](https://github.com/gosom/scrapemate) by Georgios Komninos.
The library's [MIT license](third_party/scrapemate/LICENSE) is included.
