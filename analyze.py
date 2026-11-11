"""Lead Finder: scrape businesses from Google Maps and work them as sales leads.

Usage:
    python analyze.py                     start the app (leads in data/leads.csv) and open it
    python analyze.py a.csv [b.csv ...]   use other leads files; new places go into the first
    python analyze.py --export            write <first>_export.csv and exit

Scraping is started and stopped from the page. Several CSVs are merged and
de-duplicated by Google place id.

The dashboard only reads rows appended since its last check, so it can stay
open while the scraper is still writing: new places appear on their own.
Lead status and notes entered in the dashboard are kept in <first>.leads.json,
corrections and businesses added by hand in <first>.edits.json.
"""

import argparse
import csv
import heapq
import json
import math
import os
import re
import shutil
import sys
import threading
import time
import webbrowser
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from scraper_control import ScrapeManager, clean_settings

DEFAULT_PORT = 8090
APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
DEFAULT_RESULTS = APP_DIR / "data" / "leads.csv"
SCRAPER_EXE = APP_DIR / "google-maps-scraper.exe"
DEFAULT_KEYWORDS = APP_DIR / "keywords.txt" if (APP_DIR / "keywords.txt").exists() else None
JSON_TYPE = "application/json; charset=utf-8"
UTF8_BOM = b"\xef\xbb\xbf"

STATIC_FILES = {
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/categories.json": ("categories.json", "application/json; charset=utf-8"),
}

# Domains that mean "has a link, but not a website of its own".
SOCIAL_DOMAINS = (
    "facebook.com", "fb.com", "fb.me", "instagram.com", "wa.me", "whatsapp.com",
    "youtube.com", "youtu.be", "twitter.com", "x.com", "linkedin.com", "t.me",
    "justdial.com", "indiamart.com", "tradeindia.com", "sulekha.com",
    "business.site", "google.com", "g.page", "goo.gl", "linktr.ee",
    "zomato.com", "swiggy.com", "magicpin.in", "practo.com", "booking.com",
    "makemytrip.com", "tripadvisor.com", "tripadvisor.in", "goibibo.com",
)

# Places people live in, not businesses: left out of the lead list.
RESIDENTIAL_CATEGORIES = {
    "apartment building", "apartment complex", "housing society", "housing complex",
    "housing development", "condominium complex", "residential building", "building",
    "housing cooperative", "villa", "gated community", "group home", "housing association",
}

# Business types unlikely to buy digital services (websites, WhatsApp automation): street-level
# perishables vendors, licensed liquor outlets, and places that are not businesses at all.
# Hidden from the lead list by default; the "Business fit" filter brings them back.
LOW_FIT_RE = re.compile(
    r"\b(fruits?|vegetables?|greengrocer|produce market|chicken|mutton|meat|butcher|fish|eggs?|poultry"
    r"|dairy|milk|paan|tobacco|tea|juice|soft drinks|flour mill|farm|liquor|wine|beer"
    r"|religious|temple|mosque|church|gurudwara|shrine|government|police|post office|atm"
    r"|park|bus stop|bus station|public|toilet|soup kitchen|warehouse|scrap|cemetery|crematorium)\b",
    re.IGNORECASE,
)
# Categories the pattern above catches by accident ("Dry Fruit Store", "Meat dish restaurant").
LOW_FIT_EXCEPTIONS_RE = re.compile(r"dry fruit|goods store|restaurant", re.IGNORECASE)

# "About" sections that say what a business offers (the rest is accessibility, parking, etc.).
SERVICE_SECTIONS = {
    "service_options", "offerings", "from_the_business", "highlights", "amenities",
    "popular_for", "dining_options",
}

# Business type guessed from the name when Google lists no category. First match wins,
# so specific words come before the catch-all "store / shop" rule.
TITLE_CATEGORY_RULES = [(re.compile(rf"\b({words})", re.IGNORECASE), category) for words, category in (
    (r"meat|chicken|mutton|poultry", "Chicken & Mutton Shop"),
    (r"medical|medicose|pharma|chemist|drug\b", "Pharmacy"),
    (r"clinic|hospital|dental|nursing home", "Medical clinic"),
    (r"kirana|kiryana|provision|general store|departmental|ration\b", "General store"),
    (r"bakery|bakers|cake", "Bakery and Cake Shop"),
    (r"super ?market|hypermarket", "Supermarket"),
    (r"fancy|cosmetic|bangle", "Cosmetics store"),
    (r"stationer|books?\b|photo ?copy|xerox", "Stationery store"),
    (r"tailor|boutique", "Tailor"),
    (r"dairy|dudh|milk", "Dairy store"),
    (r"mobile|telecom", "Cell phone store"),
    (r"sweet|misthan|mishthan|namkeen", "Sweet shop"),
    (r"cloth|garment|saree|readymade|fashion|collection|vastra", "Clothing store"),
    (r"jewel|jwel|abhushan", "Jewelry store"),
    (r"furniture", "Furniture store"),
    (r"hardware|sanitary|paint|electric", "Hardware store"),
    (r"salon|saloon|parlou?r|beauty|hair\b", "Beauty salon"),
    (r"restaurant|bhojnalaya|dhaba|cafe|fast food", "Restaurant"),
    (r"hotel\b|guest house", "Hotel"),
    (r"school|academy|coaching|classes|institute", "Coaching center"),
    (r"sports\b", "Sporting goods store"),
    (r"engineering|fabricat|industries|works\b", "Manufacturer"),
    (r"tea\b|chai\b", "Tea store"),
    (r"stores?\b|shop\b|mart\b|traders|enterprises|emporium", "Store"),
)]

# Trailing address parts that name the city, state or country rather than the area.
ADDRESS_TAIL_RE = re.compile(r"^(jodhpur|rajasthan|india)\b|\b\d{6}\b", re.IGNORECASE)
# An address part with any of these is a street, landmark or shop, not an area name.
NOT_A_LOCALITY_RE = re.compile(
    r"\d|\+|\b(shop|store|plot|road|rd|marg|near|opp|opposite|circle|chouraha|chauraha|highway|bypass"
    r"|lane|gali|street|floor|building|tower|complex|market|behind|front|no)\b",
    re.IGNORECASE,
)
PINCODE_RE = re.compile(r"\b34\d{4}\b")
UNKNOWN_LOCALITY = "(unknown)"
MAX_LOCALITY_WORDS = 4

# Locality from neighbours: enough of the closest places must agree, and they must be close.
NEIGHBOURS = 5
NEIGHBOUR_MAJORITY = 3
NEIGHBOUR_RADIUS_KM = 0.6
KM_PER_DEGREE = 111.0

# Bit flags in the row telling which fields are a guess rather than Google's own data.
INFERRED_CATEGORY = 1
INFERRED_LOCALITY = 2
# Further row flags: the user changed some field, or typed the whole business in.
EDITED = 4
MANUAL = 8

# Fields the user may correct in the dashboard, with their position in a row.
EDITABLE = {"title": 0, "category": 1, "locality": 2, "pincode": 3, "address": 4, "phone": 5, "website": 7, "emails": 9}
MAX_FIELD_LENGTH = 300

WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MINUTES_PER_DAY = 1440
HALF_DAY = 720
HOURS_PER_DAY = 24
RECENT_MONTHS = 3
# Upper edge in months of each review-age bucket shown in the detail panel.
AGE_BUCKETS = (3, 11, 24)  # "a year ago" is 12 months, so it lands in the 1-2 year bucket

LEAD_STATUSES = ("new", "called", "interested", "rejected", "customer")
MAX_NOTE_LENGTH = 2000
MAX_POST_BYTES = 64 * 1024
POST_PATHS = ("/lead", "/edit", "/add", "/hide", "/database/reset", "/database/backup", "/scrape/start", "/scrape/stop")

# Row layout sent to the page (see the same list in the page script).
EXPORT_COLUMNS = [
    "title", "category", "locality", "pincode", "address", "phone", "phone_type",
    "website", "website_type", "emails", "rating", "reviews", "latitude",
    "longitude", "claimed", "status", "maps_link", "details", "services", "hours",
    "lead_score", "months_since_review", "lead_status", "lead_note", "business_fit",
    "reviews_last_3_months", "low_rating_percent", "guessed_fields", "edited_by_me",
    "is_hidden",
]

INFERRED_NAMES = {0: "", INFERRED_CATEGORY: "category", INFERRED_LOCALITY: "locality", INFERRED_CATEGORY | INFERRED_LOCALITY: "category, locality"}
EDIT_NAMES = {0: "", EDITED: "edited", MANUAL: "added", EDITED | MANUAL: "added"}

WEBSITE_TYPE_NAMES = {"o": "own", "s": "social/listing", "n": "none"}
PHONE_TYPE_NAMES = {"m": "mobile", "l": "landline", "": ""}

TIME_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?(am|pm)?$")
AGO_RE = re.compile(r"(an?|\d+)\s+(minute|hour|day|week|month|year)s?\s+ago")
MONTHS_PER_UNIT = {"minute": 0, "hour": 0, "day": 0, "week": 0, "month": 1, "year": 12}


def load_json(text, default):
    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return default if value is None else value


def clean_phone(raw):
    """Return (normalized phone, type) where type is 'm', 'l' or ''."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return "", ""
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    digits = digits.lstrip("0")
    if len(digits) != 10:
        return raw.strip(), "l"
    # Indian mobile numbers start with 6-9; everything else is an STD landline.
    return "+91" + digits, "m" if digits[0] in "6789" else "l"


def website_type(url):
    if not url:
        return "n"
    host = (urlparse(url if "//" in url else "//" + url).hostname or "").lower()
    if any(host == d or host.endswith("." + d) for d in SOCIAL_DOMAINS):
        return "s"
    return "o"


def services_offered(about):
    """Flatten the enabled options of the service-related "about" sections."""
    names = []
    for section in about if isinstance(about, list) else []:
        if not isinstance(section, dict) or section.get("id") not in SERVICE_SECTIONS:
            continue
        for option in section.get("options") or []:
            if option.get("enabled") and option.get("name") not in names:
                names.append(option["name"])
    return ", ".join(names)


def compact_hours(hours):
    """Turn the per-day opening hours into one short line."""
    if not isinstance(hours, dict) or not hours:
        return ""
    # Google separates the time and am/pm with a narrow no-break space.
    days = {day: ", ".join(hours.get(day) or []).replace(" ", "") for day in WEEK}
    if len(set(days.values())) == 1:
        return "Daily: " + days[WEEK[0]]
    return "; ".join(f"{day[:3]}: {text}" for day, text in days.items() if text)


def to_minutes(text, fallback_meridiem):
    """Parse '8:30pm' into minutes after midnight; return (minutes, own meridiem)."""
    match = TIME_RE.match(text)
    meridiem = match and (match[3] or fallback_meridiem)
    if not meridiem:
        return None, None
    hour = int(match[1]) % 12 + (12 if meridiem == "pm" else 0)
    return hour * 60 + int(match[2] or 0), match[3]


def day_ranges(texts):
    """Parse one day's opening strings into [start, end] minute pairs, or None if unreadable."""
    ranges = []
    for text in texts:
        text = re.sub(r"\s", "", text).lower()
        if "24hours" in text:
            ranges.append([0, MINUTES_PER_DAY])
            continue
        if "closed" in text:
            continue
        parts = re.split("[–—-]", text)
        if len(parts) != 2:
            return None
        end, end_meridiem = to_minutes(parts[1], None)
        start, start_meridiem = to_minutes(parts[0], end_meridiem)
        if end is None or start is None:
            return None
        # "11-2pm" means 11am: the start only borrows the end's am/pm when that keeps it earlier.
        if start_meridiem is None and start >= end:
            start = (start + HALF_DAY) % MINUTES_PER_DAY
        if end <= start:
            end += MINUTES_PER_DAY  # closes after midnight
        ranges.append([start, end])
    return ranges


def week_ranges(hours):
    """Opening ranges for Monday..Sunday, or 0 when hours are missing or unreadable."""
    if not isinstance(hours, dict) or not hours:
        return 0
    week = []
    for day in WEEK:
        ranges = day_ranges(hours.get(day) or [])
        if ranges is None:
            return 0
        week.append(ranges)
    return week


def review_ages(reviews):
    """Age in months of each saved review that carries a readable date."""
    ages = []
    for review in reviews if isinstance(reviews, list) else []:
        match = AGO_RE.search(str(review.get("When") or ""))
        if match:
            count = 1 if match[1] in ("a", "an") else int(match[1])
            ages.append(count * MONTHS_PER_UNIT[match[2]])
    return ages


def months_since_review(reviews):
    """Age in months of the freshest review shown by Google, or -1 if unknown."""
    ages = review_ages(reviews)
    return min(ages) if ages else -1


def low_rating_percent(stars):
    """Share of 1 and 2 star ratings as a whole percent, or -1 without ratings."""
    total = sum(stars)
    return round(100 * (stars[0] + stars[1]) / total) if total else -1


def busy_week(popular_times):
    """Google's busy level (0-100) per hour for Monday..Sunday, or [] when not published."""
    if not isinstance(popular_times, dict) or not popular_times:
        return []
    return [
        [int((popular_times.get(day) or {}).get(str(hour)) or 0) for hour in range(HOURS_PER_DAY)]
        for day in WEEK
    ]


def lead_score(phone_kind, site_kind, claimed, reviews, rating, review_age, closed):
    """Score 0-100 for how promising a business is to contact; return (score, reasons)."""
    if closed:
        return 0, ["Marked closed on Google"]

    parts = [
        (30, "Mobile number listed") if phone_kind == "m" else
        (15, "Landline listed") if phone_kind == "l" else (0, "No phone number"),
        (25, "No website") if site_kind == "n" else
        (15, "Only a social / listing link") if site_kind == "s" else (0, "Already has own website"),
        (0, "Listing claimed by owner") if claimed else (15, "Listing not claimed by owner"),
        (15, "50+ reviews") if reviews >= 50 else
        (10, "10+ reviews") if reviews >= 10 else
        (5, "Has reviews") if reviews >= 1 else (0, "No reviews"),
        (10, "Rated 4.0 or higher") if rating >= 4 else (0, "Rating below 4.0 or missing"),
        (5, "Reviewed in the last year") if 0 <= review_age <= 12 else (0, "No recent review"),
    ]
    return sum(points for points, _ in parts), [f"+{points}  {text}" for points, text in parts]


def category_from_title(title):
    """Guess a business type from its name; '' when the name gives no clue."""
    for pattern, category in TITLE_CATEGORY_RULES:
        if pattern.search(title):
            return category
    return ""


def locality_from_address(address):
    """Pick the area name out of an address like 'Shop 4, Basni, Jodhpur, Rajasthan 342005'."""
    parts = [part.strip() for part in address.split(",") if part.strip()]
    while parts and ADDRESS_TAIL_RE.search(parts[-1]):
        parts.pop()
    # The part just before the city is the area, unless it is a street, landmark or shop name.
    if not parts or NOT_A_LOCALITY_RE.search(parts[-1]) or len(parts[-1].split()) > MAX_LOCALITY_WORDS:
        return ""
    return parts[-1]


def to_float(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


class FileTruncated(Exception):
    """A source CSV got shorter, so everything read from it so far is stale."""


class PlaceStore:
    """Places from one or more scraper CSVs, de-duplicated by Google cid.

    update() only reads the bytes appended since the previous call, so a
    growing results file is never parsed twice.
    """

    def __init__(self, sources, book=None):
        csv.field_size_limit(10**9)
        self.sources = sources
        self.book = book or EditBook(None)
        self.epoch = 0  # bumped whenever the places list is rebuilt from scratch
        self.reset()

    def reset(self):
        self.places, self.details, self.skipped, self.excluded = [], {}, 0, 0
        self.seen = set()
        self.index = {}  # place key -> position in self.places
        self.known_points = []  # (lat, lon, locality) of places whose locality is not a guess
        self.pending = []  # indexes of places still waiting for a locality from their neighbours
        self.offsets = {source: 0 for source in self.sources}
        self.headers = {source: None for source in self.sources}
        self.epoch += 1

    def update(self):
        """Read newly appended rows; return how many places were added."""
        before = len(self.places)
        try:
            for source in self.sources:
                self.read_appended(source)
        except FileTruncated:
            self.reset()
            before = 0
            for source in self.sources:
                self.read_appended(source)
        self.resolve_pending()
        for entry in self.book.added:
            self.add_manual(entry)
        return len(self.places) - before

    def resolve_pending(self):
        """Give places without a usable locality the one most of their neighbours have.

        Runs once per update, before the new rows are sent anywhere, so every
        place read so far can vote.
        """
        for index in self.pending:
            place = self.places[index]
            lat, lon = place[12], place[13]
            scale = math.cos(math.radians(lat))

            def distance(point):
                return math.hypot(point[0] - lat, (point[1] - lon) * scale) * KM_PER_DEGREE

            nearest = heapq.nsmallest(NEIGHBOURS, self.known_points, key=distance)
            votes = Counter(point[2] for point in nearest).most_common(1)
            if (len(nearest) == NEIGHBOURS and votes[0][1] >= NEIGHBOUR_MAJORITY
                    and distance(nearest[-1]) <= NEIGHBOUR_RADIUS_KM):
                place[2] = votes[0][0]
                note = f"Area guessed from nearby businesses ({votes[0][1]} of the {NEIGHBOURS} closest are in {votes[0][0]})"
            elif place[3]:
                place[2] = f"PIN {place[3]}"
                note = "Area not listed by Google and not clear from the address: grouped by pincode"
            else:
                note = "Area unknown: Google lists none and the address does not name one"
            detail = self.details[place[23]]
            detail["notes"].append(note)
            detail["base"]["locality"] = place[2]
            self.apply_edit(place[23])  # a locality typed in by the user still wins
        self.pending = []

    def apply_edit(self, key):
        """Rebuild a place from Google's values plus the user's corrections; return its row."""
        if key not in self.index:
            return None
        row, detail = self.places[self.index[key]], self.details[key]
        edit = self.book.edits.get(key) or {}
        merged = {**detail["base"], **edit}

        for field, position in EDITABLE.items():
            row[position] = merged[field]
        row[1] = row[1] or "(no category)"
        row[2] = row[2] or UNKNOWN_LOCALITY
        row[5], row[6] = clean_phone(row[5])
        row[8] = website_type(row[7])
        row[24] = 0 if LOW_FIT_RE.search(row[1]) and not LOW_FIT_EXCEPTIONS_RE.search(row[1]) else 1
        # A corrected phone or website changes how good the lead is.
        row[20], detail["why"] = lead_score(row[6], row[8], row[14], row[11], row[10], row[22], "closed" in row[15].lower())

        flags = detail["flags"]
        if "category" in edit:
            flags &= ~INFERRED_CATEGORY
        if "locality" in edit:
            flags &= ~INFERRED_LOCALITY
        row[27] = flags | (EDITED if edit else 0)
        is_hidden = 1 if key in self.book.hidden else 0
        if len(row) > 28:
            row[28] = is_hidden
        else:
            row.append(is_hidden)
        detail["edited"] = sorted(edit)
        detail["hidden"] = bool(is_hidden)
        return row

    def register(self, key, detail, flags):
        """Index a freshly appended place and layer any saved corrections on top."""
        row = self.places[-1]
        self.index[key] = len(self.places) - 1
        detail["base"] = {field: row[position] for field, position in EDITABLE.items()}
        detail["flags"] = flags
        self.details[key] = detail
        self.apply_edit(key)

    def add_manual(self, entry):
        """Add a business the user typed in; it has no Google data behind it."""
        key = entry["key"]
        if key in self.seen:
            return
        self.seen.add(key)
        self.places.append([
            entry["title"], entry["category"], entry["locality"], entry["pincode"], entry["address"],
            entry["phone"], "", entry["website"], "n", entry["emails"],
            0.0, 0, 0.0, 0.0, 0, "", "", "", "", "", 0, 0, -1, key, 1, 0, -1, MANUAL,
        ])
        self.register(key, {
            "photo": "", "plus": "", "street": "", "stars": [0] * 5, "ages": [0] * (len(AGE_BUCKETS) + 1),
            "busy": [], "hours": [], "reviews": [], "order": [], "owner": "", "why": [],
            "notes": ["Added by you - not from Google Maps"],
        }, MANUAL)

    def read_appended(self, source):
        offset = self.offsets[source]
        if not source.exists():
            return  # nothing scraped yet
        size = source.stat().st_size
        if size < offset:
            raise FileTruncated(source)
        if size == offset:
            return

        with open(source, "rb") as fh:
            fh.seek(offset)
            chunk = fh.read()

        bom = len(UTF8_BOM) if offset == 0 and chunk.startswith(UTF8_BOM) else 0
        byte_lines = chunk[bom:].splitlines(keepends=True)
        reader = csv.reader(line.decode("utf-8", errors="replace") for line in byte_lines)
        header, done = self.headers[source], 0

        for values in reader:
            end = reader.line_num
            # The last record may still be mid-write: leave it for the next call.
            unfinished = end == len(byte_lines) and not byte_lines[end - 1].endswith(b"\n")
            if header is None:
                if unfinished:
                    break
                header = self.headers[source] = values
            elif len(values) != len(header):
                if end == len(byte_lines):
                    break
                self.skipped += 1
            elif unfinished:
                break
            else:
                self.add(dict(zip(header, values)))
            done = end

        self.offsets[source] = offset + bom + sum(len(line) for line in byte_lines[:done])

    def add(self, row):
        key = row.get("cid") or row.get("link")
        if not row.get("title") or key in self.seen:
            return
        self.seen.add(key)

        category = (row.get("category") or "").strip()
        notes, inferred = [], 0
        reviews = int(to_float(row.get("review_count")))
        # No category, phone or reviews means a bare address pin ("Shop No 12"), not a business.
        address_only = not category and not row.get("phone") and not reviews
        if category.lower() in RESIDENTIAL_CATEGORIES or address_only:
            self.excluded += 1
            return

        status = (row.get("status") or "").strip()
        closed = "closed" in status.lower()
        # The status column holds either CLOSED or a one-line tagline about the business.
        tagline = "" if closed else status

        if not category:
            category = category_from_title(row["title"])
            if category:
                inferred |= INFERRED_CATEGORY
                notes.append(f'Type "{category}" guessed from the business name (Google lists no category)')

        addr = load_json(row.get("complete_address"), {})
        address = (row.get("address") or "").strip()
        pincode = (addr.get("postal_code") or "").strip()
        if not pincode and PINCODE_RE.search(address):
            pincode = PINCODE_RE.search(address)[0]
        latitude = round(to_float(row.get("latitude")), 5)
        longitude = round(to_float(row.get("longitude")), 5)

        # Google's area field is often "Landmark, Colony, Area": the last part is the area itself.
        locality = (addr.get("borough") or "").split(",")[-1].strip()
        needs_neighbours = False
        if locality:
            self.known_points.append((latitude, longitude, locality))
        else:
            inferred |= INFERRED_LOCALITY
            locality = locality_from_address(address)
            if locality:
                notes.append("Area taken from the address (Google lists no area for this place)")
            else:
                locality, needs_neighbours = UNKNOWN_LOCALITY, True
        owner = load_json(row.get("owner"), {})
        hours = load_json(row.get("open_hours"), {})
        user_reviews = load_json(row.get("user_reviews"), [])
        stars = load_json(row.get("reviews_per_rating"), {})
        stars = [int(stars.get(str(n)) or 0) for n in range(1, 6)] if isinstance(stars, dict) else [0] * 5
        ages = review_ages(user_reviews)
        phone, phone_kind = clean_phone(row.get("phone"))
        website = (row.get("website") or "").strip()
        site_kind = website_type(website)
        rating = round(to_float(row.get("review_rating")), 1)
        # The scraper always fills owner.name; only claimed listings have an id.
        claimed = 1 if (owner.get("id") or "").strip() else 0
        review_age = months_since_review(user_reviews)
        score, reasons = lead_score(phone_kind, site_kind, claimed, reviews, rating, review_age, closed)
        cid = row.get("cid") or ""

        self.places.append([
            row["title"].strip(),
            category or "(no category)",
            locality,
            pincode,
            address,
            phone,
            phone_kind,
            website,
            site_kind,
            (row.get("emails") or "").strip(),
            rating,
            reviews,
            latitude,
            longitude,
            claimed,
            status,
            f"https://www.google.com/maps?cid={cid}" if cid else row.get("link", ""),
            (row.get("descriptions") or "").strip() or tagline,
            services_offered(load_json(row.get("about"), [])),
            compact_hours(hours),
            score,
            week_ranges(hours),
            review_age,
            key,
            0 if LOW_FIT_RE.search(category) and not LOW_FIT_EXCEPTIONS_RE.search(category) else 1,
            sum(1 for age in ages if age <= RECENT_MONTHS),
            low_rating_percent(stars),
            inferred,
        ])
        if needs_neighbours:
            self.pending.append(len(self.places) - 1)

        # Bulky per-place data, fetched by the page only when a place is opened.
        self.register(key, {
            "photo": (row.get("thumbnail") or "").strip(),
            "plus": (row.get("plus_code") or "").strip(),
            "street": (row.get("street_view_url") or "").strip(),
            "stars": stars,
            # Saved reviews per age bucket: up to 3 months, 11 months, 24 months, older.
            "ages": [
                sum(1 for age in ages if low < age <= high)
                for low, high in zip((-1,) + AGE_BUCKETS, AGE_BUCKETS + (float("inf"),))
            ],
            "busy": busy_week(load_json(row.get("popular_times"), {})),
            "hours": [[day, ", ".join(hours.get(day) or [])] for day in WEEK] if isinstance(hours, dict) and hours else [],
            "reviews": [
                {
                    "n": str(review.get("Name") or ""),
                    "r": to_float(review.get("Rating")),
                    "t": str(review.get("Description") or ""),
                    "w": str(review.get("When") or ""),
                }
                for review in (user_reviews if isinstance(user_reviews, list) else [])
            ],
            "order": [
                {"link": item.get("link"), "source": item.get("source") or "link"}
                for item in load_json(row.get("order_online"), [])
                if isinstance(item, dict) and str(item.get("link") or "").startswith("http")
            ],
            "owner": (owner.get("link") or "") if claimed else "",
            "why": reasons,
            "notes": notes,
        }, inferred)


class EditBook:
    """Corrections and extra businesses typed into the dashboard, kept in a JSON file.

    The scraper's CSV is never changed: corrections are layered on top when it is read.
    """

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        saved = load_json(path.read_text(encoding="utf-8"), {}) if path and path.exists() else {}
        self.edits = saved.get("edits") or {}  # place key -> {field: corrected value}
        self.added = saved.get("added") or []  # businesses with no Google listing behind them
        self.hidden = set(saved.get("hidden") or [])

    def hide(self, key, is_hidden=True):
        with self.lock:
            if is_hidden:
                self.hidden.add(key)
            else:
                self.hidden.discard(key)
            self.save()

    def set(self, key, changed):
        with self.lock:
            if changed:
                self.edits[key] = changed
            else:
                self.edits.pop(key, None)
            self.save()

    def add(self, entry):
        with self.lock:
            self.added.append(entry)
            self.save()

    def save(self):
        # Write to a temp file first so a crash never leaves a half-written file.
        temp = self.path.with_name(self.path.name + ".tmp")
        payload = {"edits": self.edits, "added": self.added, "hidden": sorted(self.hidden)}
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.path)


def clean_fields(fields):
    """Keep only editable fields from a browser request, as trimmed, length-limited text."""
    if not isinstance(fields, dict):
        raise ValueError("fields must be an object")
    return {name: str(fields[name]).strip()[:MAX_FIELD_LENGTH] for name in EDITABLE if name in fields}


class LeadBook:
    """Lead status and notes typed into the dashboard, kept in a JSON file."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.leads = load_json(path.read_text(encoding="utf-8"), {}) if path.exists() else {}

    def set(self, key, status, note):
        with self.lock:
            if status == "new" and not note:
                self.leads.pop(key, None)
            else:
                self.leads[key] = {"status": status, "note": note, "updated": datetime.now().isoformat(timespec="seconds")}
            # Write to a temp file first so a crash never leaves a half-written lead file.
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(json.dumps(self.leads, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(temp, self.path)

    def dump(self):
        with self.lock:
            return json.dumps(self.leads, ensure_ascii=False).encode("utf-8")


def read_places(paths, book=None):
    """Read scraper CSVs once; return the filled store."""
    store = PlaceStore(paths, book)
    store.update()
    return store


def write_export_csv(path, places, leads):
    # utf-8-sig so Excel shows Hindi names correctly.
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(EXPORT_COLUMNS)
        for p in places:
            lead = leads.get(p[23], {})
            out = p[:21]
            out[6] = PHONE_TYPE_NAMES[p[6]]
            out[8] = WEBSITE_TYPE_NAMES[p[8]]
            out[10] = p[10] or ""
            out[14] = "yes" if p[14] else "no"
            writer.writerow(out + [
                p[22] if p[22] >= 0 else "", lead.get("status", "new"), lead.get("note", ""),
                "good" if p[24] else "low", p[25], p[26] if p[26] >= 0 else "",
                INFERRED_NAMES[p[27] & (INFERRED_CATEGORY | INFERRED_LOCALITY)], EDIT_NAMES[p[27] & (EDITED | MANUAL)],
            ])


def print_summary(store):
    places, skipped = store.places, store.skipped
    n = len(places)
    if not n:
        print("no places found")
        return

    def pct(count):
        return f"{count} ({100 * count / n:.0f}%)"

    rated = [p[10] for p in places if p[10] > 0]
    print(f"places           {n}" + (f"  ({skipped} incomplete rows skipped)" if skipped else ""))
    print(f"left out         {store.excluded}  (residential buildings, bare address pins)")
    print(f"low fit          {pct(sum(1 for p in places if not p[24]))}  (hidden by default in the dashboard)")
    print(f"type guessed     {pct(sum(1 for p in places if p[27] & INFERRED_CATEGORY))}  (from business name)")
    print(f"area worked out  {pct(sum(1 for p in places if p[27] & INFERRED_LOCALITY))}  (from address, neighbours or pincode)")
    print(f"area unknown     {pct(sum(1 for p in places if p[2] == UNKNOWN_LOCALITY))}")
    print(f"with phone       {pct(sum(1 for p in places if p[5]))}")
    print(f"mobile phone     {pct(sum(1 for p in places if p[6] == 'm'))}")
    print(f"own website      {pct(sum(1 for p in places if p[8] == 'o'))}")
    print(f"social link only {pct(sum(1 for p in places if p[8] == 's'))}")
    print(f"no website       {pct(sum(1 for p in places if p[8] == 'n'))}")
    print(f"unclaimed        {pct(sum(1 for p in places if not p[14]))}")
    print(f"lead score 70+   {pct(sum(1 for p in places if p[20] >= 70))}")
    if rated:
        print(f"avg rating       {sum(rated) / len(rated):.2f}  ({len(rated)} rated)")
    print("top categories   " + ", ".join(f"{c} {k}" for c, k in Counter(p[1] for p in places).most_common(5)))


class Dashboard:
    """Serves the dashboard page and streams newly scraped places to it."""

    def __init__(self, sources, leads, book, scraper):
        self.sources = sources
        self.leads = leads
        self.book = book
        self.scraper = scraper
        self.store = PlaceStore(sources, book)
        self.lock = threading.Lock()

    def changes(self, epoch, have):
        """Return the places a client is missing, given what it already holds."""
        with self.lock:
            self.store.update()
            reset = epoch != self.store.epoch or have > len(self.store.places)
            body = {
                "epoch": self.store.epoch,
                "reset": reset,
                "total": len(self.store.places),
                "modified": datetime.fromtimestamp(
                    max((source.stat().st_mtime for source in self.sources if source.exists()), default=time.time())
                ).strftime("%H:%M:%S"),
                "places": self.store.places[0 if reset else have:],
                "scrape": self.scraper.status(),
            }
        return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def detail(self, key):
        with self.lock:
            return json.dumps(self.store.details.get(key) or {}, ensure_ascii=False).encode("utf-8")

    def edit(self, key, fields, restore):
        """Save the user's corrections for one place; return its rebuilt row, or None if unknown."""
        with self.lock:
            detail = self.store.details.get(key)
            if detail is None:
                return None
            base = detail["base"]
            # A blank box for a field Google left empty is not a change.
            placeholders = ("(no category)", UNKNOWN_LOCALITY)
            changed = {} if restore else {
                name: value for name, value in fields.items()
                if value != base[name] and not (value == "" and base[name] in placeholders)
            }
            if not changed.get("title", base["title"]):
                changed.pop("title")  # a business cannot lose its name
            self.book.set(key, changed)
            return self.store.apply_edit(key)

    def add_place(self, fields):
        """Add a business that is not on Google Maps; return its key."""
        entry = {name: fields.get(name, "") for name in EDITABLE}
        if not entry["title"]:
            raise ValueError("a business needs a name")
        entry["key"] = f"manual-{time.time_ns() // 1_000_000}"
        self.book.add(entry)
        with self.lock:
            self.store.add_manual(entry)
        return entry["key"]

    def hide_place(self, key: str, is_hidden: bool = True):
        with self.lock:
            self.book.hide(key, is_hidden)
            return self.store.apply_edit(key)

    def backup_database(self) -> str:
        with self.lock:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_dir = APP_DIR / "data" / "backups" / f"backup_{ts}"
            backup_dir.mkdir(parents=True, exist_ok=True)
            for p in self.sources:
                if p.exists():
                    shutil.copy2(p, backup_dir / p.name)
                for suffix in (".leads.json", ".edits.json", ".resume.json", ".log"):
                    sidecar = p.with_name(p.name + suffix)
                    if sidecar.exists():
                        shutil.copy2(sidecar, backup_dir / sidecar.name)
            return backup_dir.name

    def reset_database(self) -> str:
        with self.lock:
            backup_name = self.backup_database()
            main_src = self.sources[0]
            if main_src.exists():
                with open(main_src, "r", encoding="utf-8", errors="replace") as fh:
                    first_line = fh.readline()
                with open(main_src, "w", encoding="utf-8") as fh:
                    if first_line.startswith("title"):
                        fh.write(first_line)
                    else:
                        fh.write("")
            for p in self.sources:
                for suffix in (".leads.json", ".edits.json", ".resume.json", ".log"):
                    sidecar = p.with_name(p.name + suffix)
                    if sidecar.exists():
                        try:
                            sidecar.unlink()
                        except OSError:
                            pass
            self.leads = LeadBook(main_src.with_name(main_src.name + ".leads.json"))
            self.book = EditBook(main_src.with_name(main_src.name + ".edits.json"))
            self.store = PlaceStore(self.sources, self.book)
            return backup_name

    def read_static(self, name: str) -> bytes | None:
        path = WEB_DIR / name
        if not path.is_file():
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    def render_page(self) -> bytes:
        html = self.read_static("index.html")
        if html is None:
            return b"<!doctype html><p>Frontend files missing in web/ directory.</p>"
        title = " + ".join(source.name for source in self.sources)
        return html.replace(b"__TITLE__", title.encode("utf-8"))

    def handler(self):
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urlparse(self.path)
                query = parse_qs(url.query)
                if url.path in ("/", "/index.html"):
                    return self.reply(dashboard.render_page(), "text/html; charset=utf-8")
                if url.path in STATIC_FILES:
                    name, content_type = STATIC_FILES[url.path]
                    data = dashboard.read_static(name)
                    if data is None:
                        return self.send_error(404)
                    return self.reply(data, content_type)
                if url.path == "/leads.json":
                    return self.reply(dashboard.leads.dump(), JSON_TYPE)
                if url.path == "/scrape.json":
                    return self.reply(json.dumps(dashboard.scraper.status(with_keywords=True), ensure_ascii=False).encode("utf-8"), JSON_TYPE)
                if url.path == "/place.json":
                    return self.reply(dashboard.detail(query.get("key", [""])[0]), "application/json; charset=utf-8")
                if url.path != "/data.json":
                    return self.send_error(404)
                try:
                    epoch = int(query.get("epoch", ["0"])[0])
                    have = int(query.get("have", ["0"])[0])
                    payload = dashboard.changes(epoch, max(have, 0))
                except ValueError:
                    return self.send_error(400)
                except OSError as err:
                    return self.send_error(500, str(err))
                self.reply(payload, "application/json; charset=utf-8")

            def do_POST(self):
                # JSON content type only: a plain cross-site form cannot send it.
                if self.path not in POST_PATHS or "application/json" not in (self.headers.get("Content-Type") or ""):
                    return self.send_error(404)
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if not 0 < length <= MAX_POST_BYTES:
                        return self.send_error(400)
                    body = json.loads(self.rfile.read(length))
                    if self.path.startswith("/scrape/"):
                        return self.scrape_action(body)
                    if self.path == "/edit":
                        row = dashboard.edit(str(body["key"]), clean_fields(body.get("fields") or {}), bool(body.get("restore")))
                        if row is None:
                            return self.send_error(404)
                        return self.reply(json.dumps({"row": row}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
                    if self.path == "/add":
                        key = dashboard.add_place(clean_fields(body.get("fields") or {}))
                        return self.reply(json.dumps({"key": key}).encode("utf-8"), "application/json; charset=utf-8")
                    if self.path == "/hide":
                        key = str(body.get("key") or "")
                        is_hidden = bool(body.get("hidden", True))
                        row = dashboard.hide_place(key, is_hidden)
                        if row is None:
                            return self.send_error(404)
                        return self.reply(json.dumps({"row": row, "hidden": is_hidden}, ensure_ascii=False).encode("utf-8"), JSON_TYPE)
                    if self.path == "/database/backup":
                        backup_name = dashboard.backup_database()
                        return self.reply(json.dumps({"status": "ok", "backup": backup_name}).encode("utf-8"), JSON_TYPE)
                    if self.path == "/database/reset":
                        if dashboard.scraper.is_running():
                            return self.reply(json.dumps({"error": "Cannot reset database while a search is running. Stop search first."}).encode("utf-8"), JSON_TYPE, 400)
                        backup_name = dashboard.reset_database()
                        return self.reply(json.dumps({"status": "ok", "backup": backup_name}).encode("utf-8"), JSON_TYPE)
                    key, status = str(body["key"]), str(body["status"])
                    note = str(body.get("note") or "").strip()[:MAX_NOTE_LENGTH]
                except (ValueError, KeyError, TypeError):
                    return self.send_error(400)
                except OSError as err:
                    return self.send_error(500, str(err))
                if not key or status not in LEAD_STATUSES:
                    return self.send_error(400)
                try:
                    dashboard.leads.set(key, status, note)
                except OSError as err:
                    return self.send_error(500, str(err))
                self.reply(b"{}", "application/json; charset=utf-8")

            def scrape_action(self, body):
                """Start or stop the scraper; problems come back as a message the page can show."""
                try:
                    if self.path == "/scrape/stop":
                        dashboard.scraper.stop()
                    else:
                        settings = body.get("settings")
                        dashboard.scraper.start(clean_settings(settings) if settings else None)
                except (ValueError, TypeError) as err:
                    return self.reply(json.dumps({"error": str(err)}).encode("utf-8"), JSON_TYPE, 400)
                self.reply(json.dumps(dashboard.scraper.status()).encode("utf-8"), JSON_TYPE)

            def reply(self, body, content_type, code=200):
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        return Handler


def main():
    parser = argparse.ArgumentParser(description="Lead finder: scrape Google Maps businesses and work them as leads.")
    parser.add_argument("csv", nargs="*", default=[str(DEFAULT_RESULTS)], help="leads file(s); new places are scraped into the first one")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="dashboard port")
    parser.add_argument("--export", action="store_true", help="write <name>_export.csv and exit")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    args = parser.parse_args()

    sources = [Path(name) for name in args.csv]
    for source in sources[1:]:
        if not source.exists():
            sys.exit(f"file not found: {source}")
    sources[0].parent.mkdir(parents=True, exist_ok=True)

    leads = LeadBook(sources[0].with_name(sources[0].name + ".leads.json"))
    book = EditBook(sources[0].with_name(sources[0].name + ".edits.json"))

    if args.export:
        store = read_places(sources, book)
        print_summary(store)
        export_path = sources[0].with_name(sources[0].stem + "_export.csv")
        write_export_csv(export_path, store.places, leads.leads)
        print(f"\nwrote {export_path}")
        return

    url = f"http://localhost:{args.port}"
    scraper = ScrapeManager(sources[0], SCRAPER_EXE, DEFAULT_KEYWORDS)
    try:
        # Local only: the dashboard exposes scraped phone numbers.
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Dashboard(sources, leads, book, scraper).handler())
    except OSError:
        print(f"Lead Finder is already running: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return

    scraper.resume_if_wanted()
    print(f"Lead Finder is running: {url}\nKeep this window open. Close it (or press Ctrl+C) to stop.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        # Stop the scraper with us, but remember to continue it on the next launch.
        scraper.stop(keep_wanted=True)


if __name__ == "__main__":
    main()
