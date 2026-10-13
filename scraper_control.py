"""Runs the Google Maps scraper for the dashboard and keeps it going until it is done.

The scraper exits on its own when Google throttles it, the network drops or it
sits idle. ScrapeManager restarts it with -resume each time, so finished
searches and already-saved places are skipped and the results file only grows.
"""

import ctypes
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

# Give up after this many passes in a row that add nothing.
MAX_IDLE_PASSES = 3
# Wait this long after a pass that added nothing, so a Google throttle can clear.
COOLDOWN_SECONDS = 15 * 60
PAUSE_BETWEEN_PASSES_SECONDS = 30
INACTIVITY = "10m"
SCROLL_DEPTH = 10
MAX_KEYWORDS = 500
MAX_KEYWORD_LENGTH = 80
CELL_KM_CHOICES = (1, 2, 3)
CONCURRENCY_RANGE = (1, 16)
# Largest search area accepted, in degrees per side (~55 km): bigger is almost surely a mistake.
MAX_BBOX_DEGREES = 0.5

# Windows: keep the PC awake while a scrape runs (ES_CONTINUOUS | ES_SYSTEM_REQUIRED).
KEEP_AWAKE = 0x80000001
ALLOW_SLEEP = 0x80000000

CELLS_RE = re.compile(r"grid scraping: ~(\d+) cells")


def set_keep_awake(flags):
    if os.name == "nt":
        ctypes.windll.kernel32.SetThreadExecutionState(flags)


def clean_settings(raw):
    """Validate scrape settings coming from the browser; raise ValueError with a readable reason."""
    if not isinstance(raw, dict):
        raise ValueError("settings must be an object")

    try:
        south, west, north, east = (round(float(value), 5) for value in raw["bbox"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Choose an area first") from None
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ValueError("The area is not a valid rectangle")
    if north - south > MAX_BBOX_DEGREES or east - west > MAX_BBOX_DEGREES:
        raise ValueError("The area is too large - pick one city, not a whole region")

    keywords = []
    for word in raw.get("keywords") or []:
        word = " ".join(str(word).split())[:MAX_KEYWORD_LENGTH]
        if word and word.lower() not in (k.lower() for k in keywords):
            keywords.append(word)
    if not keywords:
        raise ValueError("Add at least one business type")
    if len(keywords) > MAX_KEYWORDS:
        raise ValueError(f"Too many business types (limit {MAX_KEYWORDS})")

    cell = int(raw.get("cell") or 2)
    concurrency = int(raw.get("concurrency") or 8)
    if cell not in CELL_KM_CHOICES:
        raise ValueError("Unknown detail level")
    if not CONCURRENCY_RANGE[0] <= concurrency <= CONCURRENCY_RANGE[1]:
        raise ValueError("Unknown speed")

    return {
        "area": " ".join(str(raw.get("area") or "").split())[:MAX_KEYWORD_LENGTH],
        "bbox": [south, west, north, east],
        "keywords": keywords,
        "cell": cell,
        "concurrency": concurrency,
    }


class ScrapeManager:
    """Starts, watches and stops the scraper process for one results file."""

    def __init__(self, results, exe, default_keywords):
        self.results = Path(results)
        self.exe = Path(exe)
        self.default_keywords = Path(default_keywords)
        self.settings_path = self.results.with_name(self.results.name + ".scrape.json")
        self.keywords_path = self.results.with_name(self.results.name + ".keywords.txt")
        self.state_path = self.results.with_name(self.results.name + ".resume.json")
        self.log_path = self.results.with_name(self.results.name + ".log")
        self.pass_log_path = self.results.with_name(self.results.name + ".log.pass")

        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.proc = None
        self.state = "idle"  # idle | running | waiting | complete | blocked | error
        self.message = ""
        self.pass_no = 0
        self.cells = 0
        self.errors = 0
        self.settings = self.load_settings()

    def load_settings(self):
        """Saved search settings, or None when there are none or the file is damaged."""
        try:
            saved = json.loads(self.settings_path.read_text(encoding="utf-8-sig"))
            return {**clean_settings(saved), "wanted": bool(saved.get("wanted"))}
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def save_settings(self):
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(self.settings, ensure_ascii=False, indent=1), encoding="utf-8")

    def recommended_keywords(self):
        try:
            return [line.strip() for line in self.default_keywords.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
        except OSError:
            return []

    def completed_count(self):
        try:
            return len(json.loads(self.state_path.read_text(encoding="utf-8")).get("completed_inputs") or [])
        except (OSError, ValueError):
            return 0

    def status(self, with_keywords=False):
        with self.lock:
            settings = self.settings
            done = self.completed_count()
            # Searches = business types x grid squares; the square count is the scraper's own
            # rough estimate, so once everything is finished the real count replaces it.
            total = self.cells * len(settings["keywords"]) if settings and self.cells else 0
            body = {
                "state": self.state,
                "message": self.message,
                "pass": self.pass_no,
                "errors": self.errors,
                "done": done,
                "total": done if self.state == "complete" else max(total, done) if total else 0,
                "area": settings["area"] if settings else "",
                "exe_found": self.exe.exists(),
            }
            if with_keywords:
                body["settings"] = settings
                body["recommended"] = self.recommended_keywords()
            return body

    def start(self, settings=None):
        """Start scraping with new settings, or continue with the saved ones."""
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError("A scrape is already running")
            if settings is not None:
                self.settings = {**settings, "wanted": True}
            if not self.settings:
                raise ValueError("Choose an area and business types first")
            if not self.exe.exists():
                raise ValueError(f"Scraper program not found: {self.exe.name}")
            self.settings["wanted"] = True
            self.save_settings()
            self.keywords_path.write_text("\n".join(self.settings["keywords"]) + "\n", encoding="utf-8")
            self.stop_event.clear()
            self.state, self.message, self.pass_no, self.errors = "running", "Starting...", 0, 0
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def resume_if_wanted(self):
        """Continue a scrape that was still running when the dashboard was last closed."""
        if self.settings and self.settings.get("wanted") and self.exe.exists():
            self.start()

    def stop(self, keep_wanted=False):
        """Stop the scraper. keep_wanted leaves it marked to continue on the next launch."""
        self.stop_event.set()
        with self.lock:
            proc = self.proc
            if self.settings and not keep_wanted:
                self.settings["wanted"] = False
                self.save_settings()
        if proc and proc.poll() is None:
            self.kill(proc)
        thread = self.thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=15)

    @staticmethod
    def kill(proc):
        # The scraper starts browser processes of its own: end the whole tree.
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            proc.terminate()

    def command(self):
        settings = self.settings
        return [
            str(self.exe), "-input", str(self.keywords_path), "-results", str(self.results), "-resume",
            "-grid-bbox", ",".join(str(value) for value in settings["bbox"]),
            "-grid-cell", str(settings["cell"]), "-c", str(settings["concurrency"]),
            "-depth", str(SCROLL_DEPTH), "-exit-on-inactivity", INACTIVITY,
        ]

    def environment(self):
        env = dict(os.environ)
        env["DISABLE_TELEMETRY"] = "1"
        # The scraper's browser driver needs Node.js; use the installed one unless told otherwise.
        if not env.get("PLAYWRIGHT_NODEJS_PATH") and shutil.which("node"):
            env["PLAYWRIGHT_NODEJS_PATH"] = shutil.which("node")
        return env

    def set_state(self, state, message):
        with self.lock:
            self.state, self.message = state, message

    def run(self):
        set_keep_awake(KEEP_AWAKE)
        try:
            self.loop()
        except OSError as err:
            self.set_state("error", f"Could not run the scraper: {err}")
        finally:
            set_keep_awake(ALLOW_SLEEP)
            with self.lock:
                self.proc = None
                if self.state in ("running", "waiting"):
                    self.state, self.message = "idle", "Stopped"

    def loop(self):
        idle_passes = 0
        while not self.stop_event.is_set():
            with self.lock:
                self.pass_no += 1
            done_before = self.completed_count()
            size_before = self.results.stat().st_size if self.results.exists() else 0
            self.set_state("running", "Searching Google Maps")

            self.results.parent.mkdir(parents=True, exist_ok=True)
            with open(self.pass_log_path, "wb") as log:
                proc = subprocess.Popen(
                    self.command(), stdout=subprocess.DEVNULL, stderr=log, env=self.environment(),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                with self.lock:
                    self.proc = proc
                while proc.poll() is None:
                    if self.stop_event.wait(5):
                        self.kill(proc)
                        proc.wait(timeout=30)
                    self.read_pass_log()

            jobs_run = self.read_pass_log()
            self.archive_pass_log()
            if self.stop_event.is_set():
                return

            done_after = self.completed_count()
            grew = (self.results.stat().st_size if self.results.exists() else 0) > size_before
            # With every search already marked done, the scraper has nothing to queue and runs zero jobs.
            if done_after > 0 and jobs_run == 0 and proc.returncode == 0:
                with self.lock:
                    self.settings["wanted"] = False
                    self.save_settings()
                self.set_state("complete", f"Finished: all {done_after} searches done")
                return
            if proc.returncode != 0 and jobs_run == 0:
                self.set_state("error", "The scraper stopped with an error - see the log file next to your leads file")
                return

            if done_after > done_before or grew:
                idle_passes = 0
                wait = PAUSE_BETWEEN_PASSES_SECONDS
            else:
                idle_passes += 1
                if idle_passes >= MAX_IDLE_PASSES:
                    self.set_state("blocked", "Google is not answering right now. Wait a few hours, then press Continue.")
                    return
                wait = COOLDOWN_SECONDS
                self.set_state("waiting", f"Nothing new - Google may be slowing us down. Retrying in {wait // 60} minutes")
            if self.stop_event.wait(wait):
                return

    def read_pass_log(self):
        """Refresh the error and grid-size counters from the current pass log; return jobs finished."""
        try:
            text = self.pass_log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0
        cells = CELLS_RE.search(text)
        with self.lock:
            self.errors = text.count('"level":"error"')
            if cells:
                self.cells = int(cells[1])
        return text.count('"message":"job finished"')

    def archive_pass_log(self):
        try:
            with open(self.log_path, "ab") as log:
                log.write(self.pass_log_path.read_bytes())
            self.pass_log_path.unlink()
        except OSError:
            pass
