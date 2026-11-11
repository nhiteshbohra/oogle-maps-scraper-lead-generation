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

# Windows job-object constants (winnt.h), used by ChildGuard.
JOB_EXTENDED_LIMIT_INFO = 9
JOB_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
IO_COUNTER_FIELDS = 6

CELLS_RE = re.compile(r"grid scraping: ~(\d+) cells")

KEYWORD_PACKS = {
    "high_value": [
        "gym", "fitness centre", "yoga class", "salon", "beauty parlour", "spa",
        "makeup artist", "bridal makeup", "tattoo studio", "dental clinic", "clinic",
        "skin clinic", "hospital", "coaching centre", "restaurant", "cafe", "hotel",
        "resort", "banquet hall", "wedding planner", "photographer", "interior designer",
        "real estate agent", "architect", "modular kitchen", "solar panel dealer",
    ],
    "healthcare": [
        "dental clinic", "dentist", "clinic", "doctor", "hospital", "nursing home",
        "skin clinic", "eye clinic", "physiotherapy", "diagnostic centre", "pathology lab",
        "IVF centre", "ayurvedic clinic", "homeopathy clinic", "hair transplant clinic",
        "dietitian", "veterinary clinic", "pet clinic", "medical store", "ayurvedic store",
    ],
    "education": [
        "coaching centre", "coaching institute", "IELTS coaching", "tuition centre",
        "computer institute", "school", "play school", "preschool", "college",
        "driving school", "car driving school", "music class", "abacus class",
        "academy", "institute",
    ],
    "food_hospitality": [
        "restaurant", "cafe", "fast food", "dhaba", "hotel", "hotel restaurant",
        "guest house", "resort", "resort hotel", "banquet hall", "banquet",
        "marriage garden", "caterer", "catering service", "bakery", "sweet shop",
        "namkeen shop", "bakery cake shop",
    ],
    "real_estate_home": [
        "real estate agent", "real estate developer", "property dealer", "builder",
        "interior designer", "architect", "contractor", "modular kitchen",
        "furniture showroom", "tiles showroom", "electronics showroom", "solar panel dealer",
        "CCTV dealer", "pest control", "cleaning service", "AC repair", "RO service",
        "carpenter", "plumber", "electrician", "painter",
    ],
    "services_business": [
        "chartered accountant", "tax consultant", "lawyer", "insurance agency",
        "finance company", "loan agent", "packers and movers", "courier service",
        "transport company", "printing press", "flex printing", "digital printing",
        "sign board maker", "advertising agency", "digital marketing agency",
        "software company", "web design company", "security service", "placement agency",
        "travel agency", "tour operator", "taxi service", "car rental",
    ],
    "retail_showrooms": [
        "jewellers", "jewellery shop", "optician", "supermarket", "clothing store",
        "garment shop", "saree shop", "boutique", "tailor", "footwear shop",
        "mobile shop", "mobile repair shop", "electronics shop", "computer shop",
        "furniture shop", "hardware shop", "paint shop", "sanitary shop",
        "cosmetic shop", "gift shop", "toy shop", "stationery shop", "car showroom",
        "bike showroom",
    ],
}

DEFAULT_RECOMMENDED_KEYWORDS = (
    "gym", "fitness centre", "yoga class", "dance class", "salon", "beauty parlour",
    "barber shop", "spa", "makeup artist", "bridal makeup", "tattoo studio",
    "dental clinic", "dentist", "clinic", "doctor", "hospital", "nursing home",
    "skin clinic", "eye clinic", "physiotherapy", "diagnostic centre", "pathology lab",
    "IVF centre", "ayurvedic clinic", "homeopathy clinic", "hair transplant clinic",
    "dietitian", "veterinary clinic", "pet clinic", "coaching centre", "coaching institute",
    "IELTS coaching", "tuition centre", "computer institute", "school", "play school",
    "preschool", "college", "driving school", "car driving school", "music class",
    "abacus class", "restaurant", "cafe", "fast food", "dhaba", "hotel",
    "hotel restaurant", "guest house", "resort", "resort hotel", "banquet hall",
    "banquet", "marriage garden", "caterer", "catering service", "real estate agent",
    "real estate developer", "property dealer", "builder", "interior designer",
    "architect", "contractor", "travel agency", "tour operator", "taxi service",
    "car rental", "immigration consultant", "car showroom", "bike showroom",
    "two wheeler showroom", "used car dealer", "car repair", "bike repair", "car wash",
    "photographer", "wedding photographer", "photo studio", "event management",
    "wedding planner", "tent house", "DJ service", "mehndi artist",
    "chartered accountant", "tax consultant", "lawyer", "insurance agency",
    "finance company", "loan agent", "packers and movers", "packers movers",
    "courier service", "transport company", "pest control", "cleaning service",
    "laundry", "dry cleaner", "AC repair", "RO service", "printing press",
    "flex printing", "digital printing", "sign board maker", "advertising agency",
    "digital marketing agency", "software company", "web design company",
    "security service", "placement agency", "solar panel dealer", "CCTV dealer",
    "modular kitchen", "furniture showroom", "tiles showroom", "electronics showroom",
    "jewellers", "jewellery shop", "optician", "shop", "store", "kirana store",
    "general store", "grocery store", "supermarket", "provision store", "sweet shop",
    "namkeen shop", "bakery", "dry fruit shop", "spice shop", "clothing store",
    "garment shop", "saree shop", "fabric shop", "boutique", "tailor",
    "readymade garments", "footwear shop", "bag shop", "watch shop", "optical shop",
    "imitation jewellery", "cosmetic shop", "gift shop", "toy shop", "stationery shop",
    "book shop", "sports shop", "handicraft shop", "pooja samagri shop", "flower shop",
    "mobile shop", "mobile repair shop", "electronics shop", "computer shop",
    "electrical shop", "home appliances shop", "hardware shop", "paint shop",
    "sanitary shop", "tiles shop", "plywood shop", "building material shop",
    "furniture shop", "mattress shop", "utensil shop", "crockery shop", "medical store",
    "ayurvedic store", "pet shop", "auto parts shop", "tyre shop", "cycle shop",
    "wholesaler", "showroom", "hostel", "notary", "carpenter", "plumber", "electrician",
    "painter", "astrologer", "consultant", "manufacturer", "factory", "exporter",
    "handicraft exporter", "furniture manufacturer", "textile", "stone supplier",
    "marble supplier", "steel fabricator", "packaging supplier", "chemical supplier",
    "machinery dealer", "cinema", "gaming zone", "office", "company", "agency",
    "services", "studio", "workshop", "institute", "academy", "centre",
    "laptop store", "car accessories", "bakery cake shop", "boutique designer",
    "gift and toys", "mobile accessories wholesale",
)


def set_keep_awake(flags):
    if os.name == "nt":
        ctypes.windll.kernel32.SetThreadExecutionState(flags)


class ChildGuard:
    """Makes Windows end the scraper whenever this app ends, however it ends.

    Closing the app window kills Python without running any cleanup code, which
    used to leave the scraper running alone and a second one starting on the next
    launch. Processes placed in a "kill on close" job object die with their owner.
    """

    def __init__(self):
        self.job = None
        if os.name != "nt":
            return

        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits), ("IoInfo", ctypes.c_uint64 * IO_COUNTER_FIELDS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        self.kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self.kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

        job = self.kernel32.CreateJobObjectW(None, None)
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = JOB_LIMIT_KILL_ON_JOB_CLOSE
        if job and self.kernel32.SetInformationJobObject(job, JOB_EXTENDED_LIMIT_INFO, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.job = job

    def protect(self, proc):
        """Tie a started process (and the browsers it starts) to this app's lifetime."""
        if not self.job:
            return
        handle = self.kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, proc.pid)
        if handle:
            self.kernel32.AssignProcessToJobObject(self.job, handle)
            self.kernel32.CloseHandle(handle)


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

    def __init__(self, results, exe, default_keywords=None):
        self.results = Path(results)
        self.exe = Path(exe)
        self.default_keywords = Path(default_keywords) if default_keywords else None
        self.settings_path = self.results.with_name(self.results.name + ".scrape.json")
        self.keywords_path = self.results.with_name(self.results.name + ".keywords.txt")
        self.state_path = self.results.with_name(self.results.name + ".resume.json")
        self.log_path = self.results.with_name(self.results.name + ".log")
        self.pass_log_path = self.results.with_name(self.results.name + ".log.pass")

        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.guard = ChildGuard()
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
            if self.default_keywords and Path(self.default_keywords).exists():
                lines = [line.strip() for line in Path(self.default_keywords).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
                if lines:
                    return lines
        except OSError:
            pass
        return list(DEFAULT_RECOMMENDED_KEYWORDS)

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
            recent_logs = []
            if self.log_path.exists():
                try:
                    with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = f.readlines()
                        recent_logs = [l.rstrip("\r\n") for l in lines[-35:]]
                except OSError:
                    pass
            body["recent_logs"] = recent_logs
            if with_keywords:
                body["settings"] = settings
                body["recommended"] = self.recommended_keywords()
                body["packs"] = KEYWORD_PACKS
            return body

    def is_running(self):
        with self.lock:
            return self.state in ("running", "waiting")

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
                self.guard.protect(proc)
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
