package runner

import (
	"context"
	"errors"
	"flag"
	"os"
	"runtime"
	"time"

	"github.com/gosom/google-maps-scraper/internal/proxyconfig"
	"github.com/gosom/scrapemate/scrapemateapp"
)

const (
	RunModeFile = iota + 1
	RunModeInstallPlaywright
)

var (
	ErrInvalidRunMode = errors.New("invalid run mode")
)

type Runner interface {
	Run(context.Context) error
	Close(context.Context) error
}

type Config struct {
	Concurrency              int
	MaxDepth                 int
	InputFile                string
	ResultsFile              string
	JSON                     bool
	LangCode                 string
	Debug                    bool
	ExitOnInactivityDuration time.Duration
	Email                    bool
	GeoCoordinates           string
	Zoom                     int
	RunMode                  int
	Proxies                  []string
	DisablePageReuse         bool
	ExtraReviews             bool
	BrowserPoolSize          int
	MaxPagesPerBrowser       int
	Resume                   bool

	// Grid scraping — divide a bounding box into cells to bypass the ~120
	// results-per-search limit imposed by Google Maps.
	GridBBox   string  // "minLat,minLon,maxLat,maxLon"
	GridCellKm float64 // size of each grid cell in km (default: 1.0)
}

func ParseConfig() *Config {
	cfg := Config{}

	if os.Getenv("PLAYWRIGHT_INSTALL_ONLY") == "1" {
		cfg.RunMode = RunModeInstallPlaywright

		return &cfg
	}

	var (
		proxies     string
		proxiesFile string
	)

	flag.IntVar(&cfg.Concurrency, "c", min(runtime.NumCPU()/2, 1), "sets the concurrency [default: half of CPU cores]")
	flag.IntVar(&cfg.MaxDepth, "depth", 10, "maximum scroll depth in search results [default: 10]")
	flag.StringVar(&cfg.ResultsFile, "results", "stdout", "path to the results file [default: stdout]")
	flag.StringVar(&cfg.InputFile, "input", "", "path to the input file with queries (one per line)")
	flag.StringVar(&cfg.LangCode, "lang", "en", "language code for Google (e.g., 'de' for German) [default: en]")
	flag.BoolVar(&cfg.Debug, "debug", false, "enable headful crawl (opens browser window) [default: false]")
	flag.DurationVar(&cfg.ExitOnInactivityDuration, "exit-on-inactivity", 0, "exit after inactivity duration (e.g., '5m')")
	flag.BoolVar(&cfg.JSON, "json", false, "produce JSON output instead of CSV")
	flag.BoolVar(&cfg.Email, "email", false, "extract emails from websites")
	flag.StringVar(&cfg.GeoCoordinates, "geo", "", "set geo coordinates for search (e.g., '37.7749,-122.4194')")
	flag.IntVar(&cfg.Zoom, "zoom", 15, "set zoom level (0-21) for search")
	flag.StringVar(&proxies, "proxies", "", "comma separated list of proxies to use in the format protocol://user:pass@host:port example: socks5://localhost:9050 or http://user:pass@localhost:9050")
	flag.StringVar(&proxiesFile, "proxies-file", "", "path to a file containing one proxy URL per line")
	flag.BoolVar(&cfg.DisablePageReuse, "disable-page-reuse", false, "disable page reuse in playwright")
	flag.BoolVar(&cfg.ExtraReviews, "extra-reviews", false, "enable extra reviews collection")
	flag.BoolVar(&cfg.Resume, "resume", false, "resume a scrape by reading existing results and appending missing places")
	flag.StringVar(&cfg.GridBBox, "grid-bbox", "", "bounding box for grid scraping: 'minLat,minLon,maxLat,maxLon' (e.g. '40.30,-3.80,40.50,-3.60')")
	flag.Float64Var(&cfg.GridCellKm, "grid-cell", 1.0, "grid cell size in km [default: 1.0]. Use with -grid-bbox")
	flag.IntVar(&cfg.BrowserPoolSize, "browser-pool-size", 0, "number of browser contexts for JS mode; 0 derives from concurrency and pages-per-browser")
	flag.IntVar(&cfg.MaxPagesPerBrowser, "pages-per-browser", 1, "maximum concurrent pages per browser context in JS mode")

	flag.Parse()

	if cfg.InputFile == "" {
		panic("InputFile must be provided: -input <file with one query per line>")
	}

	if cfg.Concurrency < 1 {
		panic("Concurrency must be greater than 0")
	}

	if cfg.MaxDepth < 1 {
		panic("MaxDepth must be greater than 0")
	}

	if cfg.Zoom < 0 || cfg.Zoom > 21 {
		panic("Zoom must be between 0 and 21")
	}

	resolvedProxies, err := proxyconfig.Resolve(proxies, proxiesFile)
	if err != nil {
		panic(err)
	}

	cfg.Proxies = resolvedProxies
	cfg.RunMode = RunModeFile

	return &cfg
}

func AppendBrowserCapacityOptions(opts []func(*scrapemateapp.Config) error, cfg *Config) []func(*scrapemateapp.Config) error {
	if cfg.MaxPagesPerBrowser > 1 {
		opts = append(opts, scrapemateapp.WithMaxPagesPerBrowser(cfg.MaxPagesPerBrowser))
	}

	if cfg.BrowserPoolSize > 0 {
		opts = append(opts, scrapemateapp.WithBrowserPoolSize(cfg.BrowserPoolSize))
	}

	return opts
}
