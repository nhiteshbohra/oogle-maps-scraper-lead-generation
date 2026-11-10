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
DEFAULT_RESULTS = APP_DIR / "data" / "leads.csv"
SCRAPER_EXE = APP_DIR / "google-maps-scraper.exe"
DEFAULT_KEYWORDS = APP_DIR / "keywords.txt"
JSON_TYPE = "application/json; charset=utf-8"
UTF8_BOM = b"\xef\xbb\xbf"

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
POST_PATHS = ("/lead", "/edit", "/add", "/scrape/start", "/scrape/stop")

# Row layout sent to the page (see the same list in the page script).
EXPORT_COLUMNS = [
    "title", "category", "locality", "pincode", "address", "phone", "phone_type",
    "website", "website_type", "emails", "rating", "reviews", "latitude",
    "longitude", "claimed", "status", "maps_link", "details", "services", "hours",
    "lead_score", "months_since_review", "lead_status", "lead_note", "business_fit",
    "reviews_last_3_months", "low_rating_percent", "guessed_fields", "edited_by_me",
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
        detail["edited"] = sorted(edit)
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
        temp.write_text(json.dumps({"edits": self.edits, "added": self.added}, ensure_ascii=False, indent=1), encoding="utf-8")
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

    def handler(self):
        dashboard = self
        page = REPORT_TEMPLATE.replace("__TITLE__", " + ".join(source.name for source in self.sources)).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urlparse(self.path)
                query = parse_qs(url.query)
                if url.path == "/":
                    return self.reply(page, "text/html; charset=utf-8")
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


REPORT_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lead Finder</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
  --grid:#e1e0d9;--border:rgba(11,11,11,.10);--bar:#2a78d6;--wash:rgba(42,120,214,.10);--good:#006300;--crit:#d03b3b}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;
  --grid:#2c2c2a;--border:rgba(255,255,255,.10);--bar:#3987e5;--wash:rgba(57,135,229,.16);--good:#0ca30c;--crit:#e66767}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1320px;margin:0 auto;padding:20px 16px 60px}
h1{font-size:20px;margin:0}
h2{font-size:14px;margin:0 0 10px;font-weight:600}
h3{font-size:13px;margin:18px 0 6px;font-weight:600}
.sub{color:var(--muted);font-size:12px;margin:2px 0 16px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px}
.filters{position:sticky;top:0;z-index:1000;display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}
input,select,textarea,button,a.btn{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--border);
  border-radius:6px;padding:6px 8px}
.filters input,.filters select{max-width:190px}
.filters input[type=search]{width:210px;max-width:210px}
option{background:var(--surface);color:var(--ink)}
button,a.btn{cursor:pointer;text-decoration:none;display:inline-block}
button:hover,a.btn:hover,select:hover{background:var(--wash);text-decoration:none}
body.simple .adv{display:none!important}
body:not(.show-insights) .ins{display:none}
.scrape-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:space-between;margin-bottom:12px}
.actions-row{display:flex;gap:8px;flex-wrap:wrap}
button.primary{background:var(--bar);color:#fff;border-color:var(--bar)}
button.primary:hover{background:var(--bar);opacity:.88}
.progress{height:8px;background:var(--grid);border-radius:4px;overflow:hidden;margin:8px 0}
.progress div{height:100%;background:var(--bar)}
#s-map{height:220px;border-radius:8px;margin-top:8px}
#panel .form textarea{min-height:150px;margin:0}
#panel .form select{width:100%}
.presets{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}
.presets span{color:var(--muted);align-self:center;font-size:12px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:12px}
.tile .v{font-size:26px;font-weight:600;line-height:1.2}
.tile .l{color:var(--ink2);font-size:12px}
.tile .n{color:var(--muted);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;margin-bottom:12px}
.bars .row{display:grid;grid-template-columns:140px 1fr 76px;gap:8px;align-items:center;padding:3px 4px;border-radius:4px}
.bars.narrow .row{grid-template-columns:56px 1fr 70px}
.bars.narrow.wide-label .row{grid-template-columns:118px 1fr 56px}
.cols{display:flex;gap:2px;align-items:flex-end;height:90px;margin-top:8px;border-bottom:1px solid var(--grid)}
.cols .col{flex:1;height:100%;display:flex;align-items:flex-end}
.cols .col div{width:100%;min-height:1px;background:var(--bar);border-radius:3px 3px 0 0;opacity:.55}
.cols .col.now div,.cols .col:hover div{opacity:1}
.cols.ticks{height:auto;border:0;margin-top:2px;font-size:11px;color:var(--muted)}
.cols.ticks div{flex:1;white-space:nowrap;overflow:visible}
.bars .row.click{cursor:pointer}
.bars .row.click:hover{background:var(--wash)}
.bars .lab{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--ink2)}
.bars .track{height:14px;border-left:1px solid var(--grid)}
.bars .bar{height:14px;min-width:2px;background:var(--bar);border-radius:0 4px 4px 0}
.bars .val{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.bars .val small{color:var(--muted)}
.scroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}
th{color:var(--ink2);font-weight:600;white-space:nowrap}
th.sort{cursor:pointer}
th.sort:hover{background:var(--wash)}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tr.click{cursor:pointer}
tr.click:hover{background:var(--wash)}
a{color:var(--bar);text-decoration:none}
a:hover{text-decoration:underline}
.dim{color:var(--muted)}
.closed{color:var(--crit);font-size:12px}
.ok{color:var(--good)}
.tag{display:inline-block;border:1px solid var(--border);border-radius:10px;padding:0 7px;font-size:12px;color:var(--ink2);white-space:nowrap}
.bar-top{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between;margin-bottom:10px}
.pager{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.pager-nav{display:inline-flex;gap:3px;align-items:center}
.pager-btn{min-width:30px;height:28px;padding:0 7px;display:inline-flex;align-items:center;justify-content:center;border-radius:5px;font-size:12px;font-weight:500;border:1px solid var(--border);background:var(--surface);cursor:pointer;user-select:none;transition:background .15s,border-color .15s}
.pager-btn:hover:not(:disabled){background:var(--wash);border-color:var(--bar)}
.pager-btn.active{background:var(--bar);color:#fff!important;border-color:var(--bar);font-weight:700}
.pager-btn:disabled{opacity:.35;cursor:not-allowed}
.pager-ellipsis{padding:0 3px;color:var(--muted);font-size:12px}
.pager-jump{display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--ink2)}
.pager-jump input{width:48px;height:28px;padding:2px 4px;text-align:center;font-size:12px}
.pager-size{display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--ink2)}
.pager-size select{height:28px;padding:2px 6px;font-size:12px}
.bar-bottom{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between;margin-top:10px;padding-top:10px;border-top:1px solid var(--grid)}
.lead-select{border:1px solid var(--grid);border-radius:12px;padding:2px 8px;font-size:11px;font-weight:600;cursor:pointer;background-color:var(--surface);outline:none;transition:all .15s}
.lead-select.new{color:var(--muted);border-color:var(--grid)}
.lead-select.called{background:#fef3c7;color:#92400e;border-color:#fde68a}
.lead-select.interested{background:#dbeafe;color:#1e40af;border-color:#bfdbfe}
.lead-select.rejected{background:#fee2e2;color:#991b1b;border-color:#fecaca}
.lead-select.customer{background:#dcfce7;color:#166534;border-color:#bbf7d0}
@media (prefers-color-scheme:dark){
  .lead-select.called{background:#451a03;color:#fde68a;border-color:#78350f}
  .lead-select.interested{background:#172554;color:#93c5fd;border-color:#1e3a8a}
  .lead-select.rejected{background:#450a0a;color:#fca5a5;border-color:#7f1d1d}
  .lead-select.customer{background:#052e16;color:#86efac;border-color:#14532d}
}
.wa-link{display:inline-flex;align-items:center;gap:3px;background:#25d366;color:#fff!important;border-radius:4px;padding:2px 7px;font-size:11px;font-weight:600;text-decoration:none!important}
.wa-link:hover{opacity:.9}
.copy-btn{display:inline-flex;align-items:center;justify-content:center;border:none;background:transparent;cursor:pointer;color:var(--muted);padding:2px 4px;font-size:12px;border-radius:3px;vertical-align:middle}
.copy-btn:hover{color:var(--bar);background:var(--wash)}
.score-pill{display:inline-block;padding:2px 7px;border-radius:4px;font-weight:700;font-size:12px}
.score-pill.hi{background:#dcfce7;color:#166534}
.score-pill.mid{background:#fef9c3;color:#854d0e}
.score-pill.lo{background:var(--grid);color:var(--muted)}
@media (prefers-color-scheme:dark){
  .score-pill.hi{background:#052e16;color:#86efac}
  .score-pill.mid{background:#422006;color:#fde047}
}
.search-box{position:relative;display:inline-flex;align-items:center}
.search-box input{padding-right:24px}
.search-clear{position:absolute;right:6px;border:none;background:transparent;cursor:pointer;color:var(--muted);font-size:15px;padding:0 4px;display:none;line-height:1}
.search-clear:hover{color:var(--ink)}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:var(--ink);color:var(--page);padding:8px 16px;border-radius:8px;font-size:13px;font-weight:500;box-shadow:0 4px 12px rgba(0,0,0,.25);z-index:9999;opacity:0;pointer-events:none;transition:opacity .2s ease}
.toast.show{opacity:1}
#map{height:460px;border-radius:8px}
.empty{color:var(--muted);padding:20px 0;text-align:center}
#panel{position:fixed;top:0;right:0;bottom:0;width:min(440px,100vw);z-index:2000;background:var(--surface);
  border-left:1px solid var(--border);box-shadow:-8px 0 24px rgba(0,0,0,.18);padding:16px;overflow-y:auto;display:none}
#panel.open{display:block}
#panel .head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
#panel .name{font-size:17px;font-weight:600;line-height:1.25}
#panel img.photo{width:100%;max-height:200px;object-fit:cover;border-radius:8px;margin:10px 0 4px;background:var(--grid)}
#panel .kv{display:grid;grid-template-columns:82px 1fr;gap:4px 10px;margin-top:10px}
#panel .kv div:nth-child(odd){color:var(--muted)}
#panel .actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
#panel .form{display:grid;grid-template-columns:82px 1fr;gap:8px 10px;align-items:center;margin-top:12px}
#panel .form label{color:var(--muted)}
#panel .form input{width:100%}
#panel textarea{width:100%;min-height:70px;resize:vertical;margin-top:6px}
#panel .review{border-top:1px solid var(--grid);padding:8px 0}
#panel .hours td{border:0;padding:1px 10px 1px 0}
#panel .today td{font-weight:600}
#panel .why{white-space:pre;font-variant-numeric:tabular-nums;color:var(--ink2);font-size:13px}
</style>
</head>
<body>
<main>
<h1>Lead Finder</h1>
<div class="sub"><span id="total"></span> &middot; <span id="live">loading...</span></div>

<div class="card scrape-bar">
  <div id="scrape-text">Checking search status...</div>
  <div class="actions-row">
    <button id="scrape-stop" hidden>Stop search</button>
    <button id="scrape-continue" hidden>Continue search</button>
    <button id="scrape-open" class="primary">Find businesses</button>
  </div>
</div>

<div class="filters card">
  <div class="search-box">
    <input id="f-q" type="search" placeholder="Search name, type, service, address">
    <button id="f-q-clear" class="search-clear" title="Clear search">&times;</button>
  </div>
  <select id="f-cat" aria-label="Category"></select>
  <select id="f-loc" aria-label="Locality"></select>
  <select class="adv" id="f-pin" aria-label="Pincode"></select>
  <select class="adv" id="f-web" aria-label="Website">
    <option value="">Website: any</option><option value="o">Own website</option>
    <option value="s">Social / listing link only</option><option value="n">No website</option>
    <option value="sn">No own website</option>
  </select>
  <select id="f-phone" aria-label="Phone">
    <option value="">Phone: any</option><option value="y">Has phone</option><option value="m">Mobile</option>
    <option value="l">Landline</option><option value="x">No phone</option>
  </select>
  <select class="adv" id="f-rating" aria-label="Rating">
    <option value="">Rating: any</option><option value="4.5">4.5 and up</option><option value="4">4.0 and up</option>
    <option value="3.5">3.5 and up</option><option value="low">Below 3.5</option><option value="none">Unrated</option>
  </select>
  <select class="adv" id="f-rev" aria-label="Reviews">
    <option value="0">Reviews: any</option><option value="10">10+</option><option value="50">50+</option>
    <option value="100">100+</option><option value="500">500+</option>
  </select>
  <select id="f-score" aria-label="Lead score">
    <option value="0">Lead score: any</option><option value="80">80+</option><option value="70">70+</option>
    <option value="50">50+</option>
  </select>
  <select class="adv" id="f-hours" aria-label="Opening hours">
    <option value="">Hours: any</option><option value="now">Open now</option><option value="sun">Open on Sunday</option>
    <option value="24">Open 24 hours</option><option value="none">Hours not listed</option>
  </select>
  <select class="adv" id="f-active" aria-label="Recent activity">
    <option value="">Activity: any</option><option value="recent">Reviewed in last 3 months</option>
    <option value="year">Reviewed in last 12 months</option><option value="stale">No review for over a year</option>
  </select>
  <select class="adv" id="f-neg" aria-label="Unhappy customers">
    <option value="0">Low ratings: any</option><option value="20">20%+ are 1-2 star</option>
    <option value="40">40%+ are 1-2 star</option>
  </select>
  <select class="adv" id="f-claim" aria-label="Claimed">
    <option value="">Listing: any</option><option value="1">Claimed by owner</option><option value="0">Unclaimed</option>
  </select>
  <select class="adv" id="f-closed" aria-label="Closed businesses">
    <option value="">Hide closed</option><option value="all">Include closed</option><option value="only">Closed only</option>
  </select>
  <select class="adv" id="f-fit" aria-label="Business fit">
    <option value="">Good fit only</option><option value="all">Include low fit</option><option value="low">Low fit only</option>
  </select>
  <select id="f-lead" aria-label="Lead status"></select>
  <button id="more">More filters</button>
  <button id="reset">Reset</button>
  <button id="add-place">+ Add business</button>
  <button id="insights">Show insights</button>
</div>

<div class="presets">
  <span>Quick views:</span>
  <button data-preset="best">Best leads (score 70+)</button>
  <button data-preset="noweb-mobile">No website + mobile number</button>
  <button class="adv" data-preset="popular-noweb">Popular (50+ reviews), no own website</button>
  <button class="adv" data-preset="low-rated">Low rated, 10+ reviews</button>
  <button class="adv" data-preset="unclaimed">Unclaimed listings</button>
  <button data-preset="open-now">Open now with phone</button>
  <button class="adv" data-preset="active">Reviewed in last 3 months</button>
  <button class="adv" data-preset="unhappy">Many unhappy customers</button>
  <button data-preset="followup">My interested leads</button>
</div>

<div class="tiles" id="tiles"></div>

<div class="grid ins">
  <div class="card"><h2>Top categories</h2><div class="bars" id="c-cat"></div></div>
  <div class="card"><h2>Top localities</h2><div class="bars" id="c-loc"></div></div>
  <div class="card"><h2>Rating spread</h2><div class="bars" id="c-rating"></div>
    <h2 style="margin-top:18px">Online presence</h2><div class="bars" id="c-web"></div></div>
  <div class="card"><h2>Lead score spread</h2><div class="bars" id="c-score"></div>
    <h2 style="margin-top:18px">Lead status</h2><div class="bars" id="c-lead"></div></div>
</div>

<div class="card ins" style="margin-bottom:12px">
  <h2>Category breakdown <span class="dim" style="font-weight:400">- click a row to filter</span></h2>
  <div class="scroll"><table id="t-cat"></table></div>
</div>

<div class="card ins" style="margin-bottom:12px" id="map-card">
  <h2>Map <span class="dim" style="font-weight:400" id="map-note"></span></h2>
  <div id="map"></div>
</div>

<div class="card">
  <div class="bar-top">
    <div>
      <h2 style="margin:0;display:inline-block">Places <span class="dim" style="font-weight:400" id="count"></span></h2>
      <span class="dim" style="font-weight:400;margin-left:8px">- click a row for full details</span>
    </div>
    <div class="pager" id="pager-top"></div>
  </div>
  <div class="scroll"><table id="t-places"></table></div>
  <div class="bar-bottom">
    <div class="dim" id="showing-range"></div>
    <div class="pager" id="pager-bottom"></div>
  </div>
</div>
<div id="toast" class="toast"></div>
</main>

<aside id="panel" aria-label="Place details"></aside>
<datalist id="dl-category"></datalist>
<datalist id="dl-locality"></datalist>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
// Row layout: 0 title, 1 category, 2 locality, 3 pincode, 4 address, 5 phone, 6 phone type (m/l),
// 7 website, 8 website type (o/s/n), 9 emails, 10 rating, 11 reviews, 12 lat, 13 lon, 14 claimed, 15 status, 16 link,
// 17 details (description or tagline), 18 services offered, 19 opening hours text, 20 lead score,
// 21 opening ranges per weekday in minutes (Mon first) or 0, 22 months since latest review (-1 unknown), 23 place key,
// 24 business fit for digital services (1 good, 0 low), 25 saved reviews from the last 3 months,
// 26 percent of ratings that are 1-2 star (-1 without ratings),
// 27 flags (1 = category guessed from the name, 2 = locality worked out by us, 4 = edited by the user, 8 = added by the user)
function remember(name, value) { try { localStorage.setItem(name, value); } catch (err) {} }
function recall(name) { try { return localStorage.getItem(name); } catch (err) { return null; } }

let DATA = [], epoch = 0, lastAdded = '', LEADS = {}, phoneCount = new Map();
let PAGE_SIZE = 50;
const savedPs = recall('lf-pagesize');
if (savedPs !== null) { const parsed = parseInt(savedPs, 10); if (!isNaN(parsed)) PAGE_SIZE = parsed; }
const MAP_LIMIT = 4000, TOP_N = 15, REFRESH_MS = 10000, HOT_SCORE = 70, DAY_MINUTES = 1440;
const MIN_RATINGS_FOR_SHARE = 10, UNHAPPY_PERCENT = 20, FIRST_BUSY_HOUR = 6;
const MIN_LOCALITY_PLACES = 5, MAP_DELAY_MS = 150, SEARCH_DELAY_MS = 250, LIVE_MAP_INTERVAL_MS = 60000;
const BASIC_TILES = 4, SEARCHES_PER_MINUTE = 25, KM_PER_DEGREE = 111, SCRAPE_POLL_MS = 5000;
const ADVANCED_FILTERS = ['f-pin', 'f-web', 'f-rating', 'f-rev', 'f-hours', 'f-active', 'f-neg', 'f-claim', 'f-closed', 'f-fit'];
const optionCache = {};
let dirty = false;
const unhappy = r => r[26] >= UNHAPPY_PERCENT && r[11] >= MIN_RATINGS_FOR_SHARE;
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = n => n.toLocaleString('en-IN');
const pct = (a, b) => b ? Math.round(100 * a / b) + '%' : '-';
const WEB_NAMES = {o: 'Own website', s: 'Social / listing only', n: 'No website'};
const LEAD_NAMES = {new: 'Not contacted', called: 'Called', interested: 'Interested', rejected: 'Not interested', customer: 'Customer'};
const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const NUMERIC_COLS = [10, 11, 20];

function showToast(msg) {
  let t = $('toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'toast';
    t.className = 'toast';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 2200);
}

function copyText(text, label) {
  if (!text) return;
  const finish = () => showToast(label ? `${label} copied!` : 'Copied to clipboard!');
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(finish).catch(() => {
      fallbackCopy(text);
      finish();
    });
  } else {
    fallbackCopy(text);
    finish();
  }
}

function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand('copy'); } catch (_) {}
  ta.remove();
}

function copyLeadSummary(key) {
  const r = DATA.find(x => x[23] === key);
  if (!r) return;
  const status = leadStatus(r);
  const text = `${r[0]}\nCategory: ${r[1]}\nPhone: ${r[5] || 'None'}\nAddress: ${r[4] || 'None'}\nWebsite: ${r[7] || 'None'}\nRating: ${r[10] > 0 ? r[10] + ' (' + r[11] + ' reviews)' : 'None'}\nLead score: ${r[20]}/100\nStatus: ${LEAD_NAMES[status] || status}`;
  copyText(text, 'Lead details');
}

async function updateLeadStatus(key, status) {
  const r = DATA.find(x => x[23] === key);
  const title = r ? r[0] : 'Lead';
  const existing = LEADS[key] || {};
  const note = existing.note || '';
  try {
    const res = await fetch('/lead', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key, status, note})
    });
    if (!res.ok) throw new Error(res.status);
    if (status === 'new' && !note) {
      delete LEADS[key];
    } else {
      LEADS[key] = {status, note, updated: new Date().toISOString().slice(0, 19)};
    }
    showToast(`${title}: ${LEAD_NAMES[status]}`);
    filterRows();
    renderTiles();
    renderCharts();
    renderPlaces();
  } catch (err) {
    showToast('Failed to save status - is analyze.py running?');
  }
}

function getPagePills(currPage, totalPages) {
  if (totalPages <= 7) {
    return Array.from({length: totalPages}, (_, i) => i);
  }
  const pills = [];
  pills.push(0);
  if (currPage > 2) pills.push('...');
  const start = Math.max(1, currPage - 1);
  const end = Math.min(totalPages - 2, currPage + 1);
  for (let i = start; i <= end; i++) {
    pills.push(i);
  }
  if (currPage < totalPages - 3) pills.push('...');
  pills.push(totalPages - 1);
  return pills;
}

function renderPager(id, currPage, totalPages) {
  const container = $(id);
  if (!container) return;

  const pills = getPagePills(currPage, totalPages);
  const pillHtml = pills.map(p => {
    if (p === '...') return `<span class="pager-ellipsis">&hellip;</span>`;
    const num = p + 1;
    const isAct = p === currPage;
    return `<button class="pager-btn${isAct ? ' active' : ''}" data-page="${p}" title="Page ${num}">${num}</button>`;
  }).join('');

  container.innerHTML = `
    <div class="pager-size">
      <span class="dim">Rows:</span>
      <select class="p-size-select" title="Rows per page">
        <option value="25"${PAGE_SIZE === 25 ? ' selected' : ''}>25</option>
        <option value="50"${PAGE_SIZE === 50 ? ' selected' : ''}>50</option>
        <option value="100"${PAGE_SIZE === 100 ? ' selected' : ''}>100</option>
        <option value="250"${PAGE_SIZE === 250 ? ' selected' : ''}>250</option>
        <option value="-1"${PAGE_SIZE === -1 ? ' selected' : ''}>All</option>
      </select>
    </div>
    <div class="pager-nav">
      <button class="pager-btn p-first" title="First page (Home)" ${currPage === 0 ? 'disabled' : ''}>&laquo;</button>
      <button class="pager-btn p-prev" title="Previous page (Left arrow)" ${currPage === 0 ? 'disabled' : ''}>&lsaquo;</button>
      <div class="pager-pills" style="display:inline-flex;gap:3px;align-items:center">${pillHtml}</div>
      <button class="pager-btn p-next" title="Next page (Right arrow)" ${currPage >= totalPages - 1 ? 'disabled' : ''}>&rsaquo;</button>
      <button class="pager-btn p-last" title="Last page (End)" ${currPage >= totalPages - 1 ? 'disabled' : ''}>&raquo;</button>
    </div>
    <div class="pager-jump">
      <span class="dim">Page:</span>
      <input type="number" class="p-jump-input" min="1" max="${totalPages}" value="${currPage + 1}" title="Type page number and press Enter">
    </div>
    <button class="p-export-btn" title="Export matching places as CSV">Export CSV</button>
  `;

  const sizeSelect = container.querySelector('.p-size-select');
  if (sizeSelect) {
    sizeSelect.onchange = e => {
      PAGE_SIZE = +e.target.value;
      remember('lf-pagesize', PAGE_SIZE);
      page = 0;
      renderPlaces();
    };
  }
  const btnFirst = container.querySelector('.p-first');
  if (btnFirst) btnFirst.onclick = () => { if (page > 0) { page = 0; renderPlaces(); } };
  const btnPrev = container.querySelector('.p-prev');
  if (btnPrev) btnPrev.onclick = () => { if (page > 0) { page--; renderPlaces(); } };
  const btnNext = container.querySelector('.p-next');
  if (btnNext) btnNext.onclick = () => { if (page < totalPages - 1) { page++; renderPlaces(); } };
  const btnLast = container.querySelector('.p-last');
  if (btnLast) btnLast.onclick = () => { if (page < totalPages - 1) { page = totalPages - 1; renderPlaces(); } };

  container.querySelectorAll('.pager-pills button').forEach(btn => {
    btn.onclick = () => {
      page = +btn.dataset.page;
      renderPlaces();
    };
  });

  const jumpInput = container.querySelector('.p-jump-input');
  if (jumpInput) {
    jumpInput.onkeydown = e => {
      if (e.key === 'Enter') {
        const val = parseInt(jumpInput.value, 10);
        if (val >= 1 && val <= totalPages) {
          page = val - 1;
          renderPlaces();
        } else {
          jumpInput.value = currPage + 1;
        }
      }
    };
  }

  const exportBtn = container.querySelector('.p-export-btn');
  if (exportBtn) exportBtn.onclick = exportCsv;
}

let sortCol = 20, sortDir = -1, page = 0, rows = [], openKey = '';

const isClosed = r => /closed/i.test(r[15]);
const leadStatus = r => (LEADS[r[23]] || {}).status || 'new';
const today = () => (new Date().getDay() + 6) % 7;  // Monday = 0, like the data

// true / false when hours are known, null when Google lists none.
function openNow(r) {
  const week = r[21];
  if (!week) return null;
  const now = new Date(), day = today(), minute = now.getHours() * 60 + now.getMinutes();
  if (week[day].some(([start, end]) => start <= minute && minute < end)) return true;
  // Still inside yesterday's late shift (a range that runs past midnight).
  return week[(day + 6) % 7].some(([, end]) => end > DAY_MINUTES && minute < end - DAY_MINUTES);
}
const alwaysOpen = r => !!r[21] && r[21].every(d => d.length === 1 && d[0][0] === 0 && d[0][1] === DAY_MINUTES);

function countBy(list, idx) {
  const m = new Map();
  for (const r of list) m.set(r[idx], (m.get(r[idx]) || 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

// minCount keeps one-off values out of the list: a dropdown with thousands of options opens slowly.
function fillSelect(id, idx, label, minCount = 1) {
  const el = $(id), current = el.value;
  const html = `<option value="">${label}: all</option>` + countBy(DATA, idx)
    .filter(([k, n]) => k !== '' && (n >= minCount || k === current))
    .map(([k, n]) => `<option value="${esc(k)}">${esc(k)} (${n})</option>`).join('');
  if (html === optionCache[id]) return;
  optionCache[id] = html;
  el.innerHTML = html;
  el.value = current;
}

function fillSelects() {
  fillSelect('f-cat', 1, 'Category');
  fillSelect('f-loc', 2, 'Locality', MIN_LOCALITY_PLACES);
  fillSelect('f-pin', 3, 'Pincode');
  // Suggestions for the edit form, so corrected values match existing spellings.
  for (const [id, idx] of [['dl-category', 1], ['dl-locality', 2]]) {
    $(id).innerHTML = countBy(DATA, idx).slice(0, 400).map(([k]) => `<option value="${esc(k)}">`).join('');
  }
}

function countPhones() {
  phoneCount = new Map();
  for (const r of DATA) if (r[5]) phoneCount.set(r[5], (phoneCount.get(r[5]) || 0) + 1);
}

function applyFilters() { page = 0; filterRows(); render(); }

// Typing or an open dropdown must not be disturbed by a background refresh.
const userBusy = () => ['SELECT', 'INPUT', 'TEXTAREA'].includes((document.activeElement || {}).tagName);

// Redraw after new places arrived from the scraper.
function showNewData() {
  if (userBusy()) { dirty = true; return; }
  dirty = false;
  fillSelects();
  filterRows();
  render(Date.now() - lastMapDraw < LIVE_MAP_INTERVAL_MS);
}

function filterRows() {
  const v = id => $(id).value;
  const q = v('f-q').trim().toLowerCase(), cat = v('f-cat'), loc = v('f-loc'), pin = v('f-pin');
  const web = v('f-web'), phone = v('f-phone'), rating = v('f-rating'), hours = v('f-hours');
  const minRev = +v('f-rev'), minScore = +v('f-score'), claim = v('f-claim'), closed = v('f-closed'), lead = v('f-lead'), fit = v('f-fit');
  const active = v('f-active'), minNeg = +v('f-neg');
  rows = DATA.filter(r => {
    if (cat && r[1] !== cat) return false;
    if (loc && r[2] !== loc) return false;
    if (pin && r[3] !== pin) return false;
    if (web && !web.includes(r[8])) return false;
    if (phone === 'y' && !r[5]) return false;
    if (phone === 'x' && r[5]) return false;
    if ((phone === 'm' || phone === 'l') && r[6] !== phone) return false;
    if (rating === 'none' ? r[10] > 0 : rating === 'low' ? !(r[10] > 0 && r[10] < 3.5) : rating && r[10] < +rating) return false;
    if (r[11] < minRev || r[20] < minScore) return false;
    if (claim && r[14] !== +claim) return false;
    if (active === 'recent' && !r[25]) return false;
    if (active === 'year' && !(r[22] >= 0 && r[22] <= 12)) return false;
    if (active === 'stale' && !(r[22] > 12)) return false;
    // A low-rating share only means something with enough ratings behind it.
    if (minNeg && !(r[26] >= minNeg && r[11] >= MIN_RATINGS_FOR_SHARE)) return false;
    if (fit === '' && !r[24]) return false;
    if (fit === 'low' && r[24]) return false;
    if (closed === '' && isClosed(r)) return false;
    if (closed === 'only' && !isClosed(r)) return false;
    if (hours === 'now' && openNow(r) !== true) return false;
    if (hours === 'sun' && !(r[21] && r[21][6].length)) return false;
    if (hours === '24' && !alwaysOpen(r)) return false;
    if (hours === 'none' && r[21]) return false;
    if (lead && leadStatus(r) !== lead) return false;
    if (q && !(r[0] + ' ' + r[1] + ' ' + r[4] + ' ' + r[5] + ' ' + r[17] + ' ' + r[18]).toLowerCase().includes(q)) return false;
    return true;
  });
}

function bars(id, items, total, filterId) {
  const max = Math.max(1, ...items.map(i => i[1]));
  $(id).innerHTML = items.length ? items.map(([label, n, value]) =>
    `<div class="row${filterId ? ' click' : ''}" data-v="${esc(value ?? label)}" title="${esc(label)}: ${fmt(n)} (${pct(n, total)})">
      <div class="lab">${esc(label)}</div>
      <div class="track"><div class="bar" style="width:${100 * n / max}%"></div></div>
      <div class="val">${fmt(n)} <small>${pct(n, total)}</small></div></div>`).join('') : '<div class="empty">No places match</div>';
  if (filterId) $(id).querySelectorAll('.row').forEach(el => el.onclick = () => { $(filterId).value = el.dataset.v; applyFilters(); });
}

function median(nums) {
  if (!nums.length) return 0;
  const s = [...nums].sort((a, b) => a - b), m = s.length >> 1;
  return s.length % 2 ? s[m] : Math.round((s[m - 1] + s[m]) / 2);
}

function renderTiles() {
  const n = rows.length, rated = rows.filter(r => r[10] > 0);
  const c = f => rows.filter(f).length;
  const avg = rated.length ? (rated.reduce((s, r) => s + r[10], 0) / rated.length).toFixed(2) : '-';
  const known = c(r => r[21]);
  const tiles = [
    [fmt(n), 'Places', pct(n, DATA.length) + ' of all scraped'],
    [fmt(c(r => r[20] >= HOT_SCORE)), 'Best leads', 'lead score ' + HOT_SCORE + ' or more'],
    [pct(c(r => r[5]), n), 'Have a phone', fmt(c(r => r[6] === 'm')) + ' mobile'],
    [pct(c(r => r[8] === 'n'), n), 'No website', fmt(c(r => r[8] === 's')) + ' more with social link only'],
    [pct(c(r => !r[14]), n), 'Unclaimed listings', fmt(c(r => !r[14])) + ' places'],
    [avg, 'Average rating', fmt(rated.length) + ' rated, median ' + fmt(median(rows.map(r => r[11]))) + ' reviews'],
    [fmt(c(r => openNow(r) === true)), 'Open right now', 'of ' + fmt(known) + ' with listed hours'],
    [fmt(c(r => r[25] > 0)), 'Reviewed in last 3 months', fmt(c(r => r[22] > 12)) + ' silent for over a year'],
    [fmt(c(unhappy)), 'Many unhappy customers', UNHAPPY_PERCENT + '%+ of ratings are 1-2 star'],
    [fmt(c(r => leadStatus(r) !== 'new')), 'Leads worked', fmt(c(r => leadStatus(r) === 'interested')) + ' interested'],
  ];
  $('tiles').innerHTML = tiles.map((t, i) => `<div class="card tile${i >= BASIC_TILES ? ' adv' : ''}"><div class="v">${t[0]}</div><div class="l">${t[1]}</div><div class="n">${t[2]}</div></div>`).join('');
}

function renderCharts() {
  const n = rows.length;
  const binned = bins => bins.map(([label, test, value]) => [label, rows.filter(test).length, value]);
  bars('c-cat', countBy(rows, 1).slice(0, TOP_N), n, 'f-cat');
  bars('c-loc', countBy(rows, 2).slice(0, TOP_N), n, 'f-loc');
  bars('c-rating', binned([['4.5 - 5.0', r => r[10] >= 4.5], ['4.0 - 4.4', r => r[10] >= 4 && r[10] < 4.5], ['3.5 - 3.9', r => r[10] >= 3.5 && r[10] < 4],
    ['3.0 - 3.4', r => r[10] >= 3 && r[10] < 3.5], ['Below 3.0', r => r[10] > 0 && r[10] < 3], ['Unrated', r => !(r[10] > 0)]]), n);
  bars('c-web', ['o', 's', 'n'].map(k => [WEB_NAMES[k], rows.filter(r => r[8] === k).length, k]), n, 'f-web');
  bars('c-score', binned([['80 - 100', r => r[20] >= 80], ['70 - 79', r => r[20] >= 70 && r[20] < 80], ['50 - 69', r => r[20] >= 50 && r[20] < 70],
    ['30 - 49', r => r[20] >= 30 && r[20] < 50], ['Below 30', r => r[20] < 30]]), n);
  bars('c-lead', Object.keys(LEAD_NAMES).map(k => [LEAD_NAMES[k], rows.filter(r => leadStatus(r) === k).length, k]), n, 'f-lead');
}

function renderCategoryTable() {
  const groups = new Map();
  for (const r of rows) { if (!groups.has(r[1])) groups.set(r[1], []); groups.get(r[1]).push(r); }
  const top = [...groups.entries()].sort((a, b) => b[1].length - a[1].length).slice(0, 30);
  $('t-cat').innerHTML = '<tr><th>Category</th><th class="num">Places</th><th class="num">Best leads</th><th class="num">Avg rating</th><th class="num">Median reviews</th>' +
    '<th class="num">Have phone</th><th class="num">No own website</th><th class="num">Unclaimed</th></tr>' +
    top.map(([cat, list]) => {
      const rated = list.filter(r => r[10] > 0);
      return `<tr class="click" data-v="${esc(cat)}"><td>${esc(cat)}</td><td class="num">${fmt(list.length)}</td>
        <td class="num">${fmt(list.filter(r => r[20] >= HOT_SCORE).length)}</td>
        <td class="num">${rated.length ? (rated.reduce((s, r) => s + r[10], 0) / rated.length).toFixed(2) : '-'}</td>
        <td class="num">${fmt(median(list.map(r => r[11])))}</td>
        <td class="num">${pct(list.filter(r => r[5]).length, list.length)}</td>
        <td class="num">${pct(list.filter(r => r[8] !== 'o').length, list.length)}</td>
        <td class="num">${pct(list.filter(r => !r[14]).length, list.length)}</td></tr>`;
    }).join('');
  $('t-cat').querySelectorAll('tr.click').forEach(el => el.onclick = () => { $('f-cat').value = el.dataset.v; applyFilters(); });
}

const COLS = [['Name and address', 0], ['What they do', 1], ['Locality', 2], ['Phone', 5], ['Website', 8], ['Rating', 10, 1], ['Reviews', 11, 1], ['Score', 20, 1], ['Lead', -1]];

const waLink = r => r[6] === 'm' ? `https://wa.me/${r[5].replace(/\D/g, '')}` : '';
const siteLink = r => /^https?:\/\//i.test(r[7]) ? `<a href="${esc(r[7])}" target="_blank" rel="noopener">${r[8] === 's' ? 'social link' : 'website'}</a>` : '<span class="dim">none</span>';

function renderPlaces() {
  const sorted = [...rows].sort((a, b) => {
    const x = a[sortCol], y = b[sortCol];
    return (typeof x === 'number' ? x - y : String(x).localeCompare(String(y))) * sortDir || b[11] - a[11];
  });
  const pageSize = PAGE_SIZE === -1 ? (sorted.length || 1) : PAGE_SIZE;
  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  page = Math.min(Math.max(0, page), pages - 1);
  const startIdx = page * pageSize;
  const endIdx = PAGE_SIZE === -1 ? sorted.length : Math.min(startIdx + pageSize, sorted.length);
  const visibleRows = sorted.slice(startIdx, endIdx);

  const head = '<tr>' + COLS.map(([name, idx, num]) => idx < 0 ? `<th>${name}</th>` :
    `<th class="sort${num ? ' num' : ''}" data-i="${idx}">${name}${sortCol === idx ? (sortDir > 0 ? ' &#9650;' : ' &#9660;') : ''}</th>`).join('') + '</tr>';
  const body = visibleRows.map(r => {
    const open = openNow(r), status = leadStatus(r);
    const scoreCls = r[20] >= 80 ? 'hi' : r[20] >= 50 ? 'mid' : 'lo';
    const wa = waLink(r);
    const leadOpts = Object.keys(LEAD_NAMES).map(k =>
      `<option value="${k}"${status === k ? ' selected' : ''}>${LEAD_NAMES[k]}</option>`
    ).join('');
    return `<tr class="click" data-key="${esc(r[23])}">
      <td>
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:4px">
          <b>${esc(r[0])}</b>
          <button class="copy-btn" title="Copy business info" onclick="event.stopPropagation();copyLeadSummary('${esc(r[23])}')">&#128203;</button>
        </div>
        ${r[27] & 8 ? ' <span class="tag">added by me</span>' : r[27] & 4 ? ' <span class="tag">edited</span>' : ''}
        <div class="dim">${esc(r[4])}${r[3] && !r[4].includes(r[3]) ? ' - ' + esc(r[3]) : ''}</div>
        ${isClosed(r) ? `<div class="closed">${esc(r[15])}</div>` : ''}
      </td>
      <td>
        ${esc(r[1])}${r[27] & 1 ? ' <span class="dim">(guessed)</span>' : ''}
        ${r[17] ? `<div class="dim">${esc(r[17])}</div>` : ''}
        ${r[18] ? `<div class="dim">Offers: ${esc(r[18])}</div>` : ''}
        ${r[19] ? `<div class="dim">${esc(r[19])}${open === null ? '' : open ? ' <span class="ok">(open now)</span>' : ' (closed now)'}</div>` : ''}
      </td>
      <td>${esc(r[2])}${r[27] & 2 && r[2] !== '(unknown)' ? ' <span class="dim">(worked out)</span>' : ''}</td>
      <td style="white-space:nowrap">
        ${r[5] ? `
          <div style="display:flex;align-items:center;gap:3px">
            <a href="tel:${esc(r[5])}" onclick="event.stopPropagation()">${esc(r[5])}</a>
            <button class="copy-btn" title="Copy phone" onclick="event.stopPropagation();copyText('${esc(r[5])}', 'Phone')">&#128203;</button>
          </div>
          ${wa ? `<div style="margin-top:3px"><a class="wa-link" href="${wa}" target="_blank" rel="noopener" onclick="event.stopPropagation()">&#128172; WhatsApp</a></div>` : ''}
        ` : '<span class="dim">none</span>'}
      </td>
      <td>${siteLink(r)}</td>
      <td class="num">
        ${r[10] > 0 ? `<span style="color:#d97706">&#9733;</span> ${r[10].toFixed(1)}` : '<span class="dim">-</span>'}
        ${unhappy(r) ? `<div class="closed" style="font-size:11px">${r[26]}% low</div>` : ''}
      </td>
      <td class="num">${fmt(r[11])}${r[25] ? `<div class="dim">${r[25]} recent</div>` : ''}</td>
      <td class="num"><span class="score-pill ${scoreCls}">${r[20]}</span></td>
      <td>
        <select class="lead-select ${status}" onclick="event.stopPropagation()" onchange="updateLeadStatus('${esc(r[23])}', this.value)" title="Change lead status">
          ${leadOpts}
        </select>
      </td>
    </tr>`;
  }).join('');
  $('t-places').innerHTML = head + (body || '<tr><td colspan="9" class="empty">No places match these filters</td></tr>');
  $('t-places').querySelectorAll('th.sort').forEach(th => th.onclick = () => {
    const i = +th.dataset.i;
    if (sortCol === i) sortDir = -sortDir; else { sortCol = i; sortDir = NUMERIC_COLS.includes(i) ? -1 : 1; }
    page = 0; renderPlaces();
  });
  $('t-places').querySelectorAll('tr.click').forEach(tr => tr.onclick = ev => {
    if (!ev.target.closest('a, button, select')) openPlace(tr.dataset.key);
  });
  const rangeText = sorted.length === 0 ? '0 places' :
    `Showing ${fmt(startIdx + 1)}–${fmt(endIdx)} of ${fmt(sorted.length)} places`;
  $('count').textContent = `- ${fmt(rows.length)} match`;
  const rangeEl = $('showing-range');
  if (rangeEl) rangeEl.textContent = rangeText;

  renderPager('pager-top', page, pages);
  renderPager('pager-bottom', page, pages);
}

let map, layer, fitted = false, mapTimer = 0, lastMapDraw = 0;
function renderMap() {
  lastMapDraw = Date.now();
  if (!window.L) { $('map-card').style.display = 'none'; return; }
  if (!map) {
    map = L.map('map', {preferCanvas: true});
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'}).addTo(map);
    layer = L.layerGroup().addTo(map);
  }
  layer.clearLayers();
  const pts = rows.filter(r => r[12] && r[13]);
  // Show the best leads first when there are too many to draw.
  const shown = pts.length > MAP_LIMIT ? [...pts].sort((a, b) => b[20] - a[20] || b[11] - a[11]).slice(0, MAP_LIMIT) : pts;
  const color = getComputedStyle(document.documentElement).getPropertyValue('--bar').trim();
  for (const r of shown) {
    L.circleMarker([r[12], r[13]], {radius: 5, weight: 1, color: '#fff', fillColor: color, fillOpacity: .85})
      .bindPopup(`<b>${esc(r[0])}</b><br>${esc(r[1])}<br>${esc(r[4])}<br>${r[10] > 0 ? r[10].toFixed(1) + ' rating, ' + fmt(r[11]) + ' reviews<br>' : ''}${esc(r[5])}
        <br>Lead score ${r[20]} &middot; <a href="#" data-open="${esc(r[23])}">Full details</a>`)
      .addTo(layer);
  }
  $('map-note').textContent = pts.length > MAP_LIMIT ? `- showing the ${fmt(MAP_LIMIT)} best leads of ${fmt(pts.length)}` : `- ${fmt(pts.length)} places`;
  if (shown.length && !fitted) {
    // Fit to the dense middle so a few far-away results don't zoom the map out.
    const q = (arr, p) => arr[Math.floor((arr.length - 1) * p)];
    const lats = shown.map(r => r[12]).sort((a, b) => a - b), lons = shown.map(r => r[13]).sort((a, b) => a - b);
    map.fitBounds([[q(lats, .02), q(lons, .02)], [q(lats, .98), q(lons, .98)]]);
    fitted = true;
  }
}

// The map is the slow part, so it is drawn after the rest of the page has updated.
function scheduleMap() {
  clearTimeout(mapTimer);
  mapTimer = setTimeout(renderMap, MAP_DELAY_MS);
}

const insightsShown = () => document.body.classList.contains('show-insights');

function render(skipMap) {
  renderTiles(); renderPlaces();
  // Charts and map are hidden until asked for; skip the work while they are.
  if (!insightsShown()) return;
  renderCharts(); renderCategoryTable();
  if (!skipMap) scheduleMap();
}

// ---- place detail panel ----
async function openPlace(key) {
  const r = DATA.find(x => x[23] === key);
  if (!r) return;
  openKey = key;
  $('panel').classList.add('open');
  $('panel').innerHTML = `<div class="head"><div class="name">${esc(r[0])}</div><button id="p-close" aria-label="Close">Close</button></div><p class="dim">Loading...</p>`;
  $('p-close').onclick = closePlace;
  let d = {};
  try { d = await (await fetch('/place.json?key=' + encodeURIComponent(key))).json(); } catch (err) { /* panel still shows the basics */ }
  if (openKey !== key) return;

  const lead = LEADS[key] || {}, open = openNow(r), sameNumber = r[5] ? (phoneCount.get(r[5]) || 1) - 1 : 0;
  const starTotal = (d.stars || []).reduce((s, n) => s + n, 0);
  const ageTotal = (d.ages || []).reduce((s, n) => s + n, 0);
  const link = (href, text) => `<a class="btn" href="${esc(href)}" target="_blank" rel="noopener">${text}</a>`;
  const kv = (k, v) => v ? `<div>${k}</div><div>${v}</div>` : '';
  $('panel').innerHTML = `
    <div class="head"><div><div class="name">${esc(r[0])}</div><div class="dim">${esc(r[1])}</div></div><button id="p-close" aria-label="Close">Close</button></div>
    ${isClosed(r) ? `<div class="closed">${esc(r[15])}</div>` : ''}
    ${d.photo ? `<img class="photo" src="${esc(d.photo)}" alt="" referrerpolicy="no-referrer" onerror="this.remove()">` : ''}
    <div class="actions">
      ${r[5] ? `<a class="btn" href="tel:${esc(r[5])}">Call ${esc(r[5])}</a>` : ''}
      ${waLink(r) ? link(waLink(r), 'WhatsApp') : ''}
      ${r[16] ? link(r[16], 'Google Maps') : ''}
      <button id="p-edit">Edit details</button>
      ${/^https?:\/\//i.test(r[7]) ? link(r[7], r[8] === 's' ? 'Social link' : 'Website') : ''}
      ${d.street ? link(d.street, 'Street View') : ''}
    </div>
    <div class="kv">
      ${kv('Address', esc(r[4]))}
      ${kv('Locality', esc(r[2]) + (r[3] ? ' - ' + esc(r[3]) : ''))}
      ${kv('Plus code', esc(d.plus || ''))}
      ${kv('Phone', r[5] ? esc(r[5]) + (r[6] === 'm' ? ' (mobile)' : ' (landline)') + (sameNumber ? `<div class="dim">Same number on <a href="#" id="p-same">${sameNumber} other listing${sameNumber > 1 ? 's' : ''}</a></div>` : '') : '<span class="dim">not listed</span>')}
      ${kv('Email', esc(r[9]))}
      ${kv('About', esc(r[17]))}
      ${kv('Offers', esc(r[18]))}
      ${kv('Listing', r[14] ? 'Claimed by owner' + (d.owner ? ` - <a href="${esc(d.owner)}" target="_blank" rel="noopener">profile</a>` : '') : 'Not claimed by owner')}
      ${kv('Classified', (d.notes || []).concat(d.edited && d.edited.length ? ['Edited by you: ' + d.edited.join(', ')] : []).map(esc).join('<br>'))}
      ${kv('Order online', (d.order || []).map(o => `<a href="${esc(o.link)}" target="_blank" rel="noopener">${esc(o.source)}</a>`).join(', '))}
    </div>

    <h3>Lead score ${r[20]} / 100</h3>
    <div class="why">${esc((d.why || []).join('\n'))}</div>

    <h3>My lead notes</h3>
    <select id="p-status">${Object.keys(LEAD_NAMES).map(k => `<option value="${k}"${(lead.status || 'new') === k ? ' selected' : ''}>${LEAD_NAMES[k]}</option>`).join('')}</select>
    <textarea id="p-note" placeholder="Notes: who you spoke to, what they said, follow-up date">${esc(lead.note || '')}</textarea>
    <div class="actions" style="margin-top:6px"><button id="p-save">Save</button><span id="p-saved" class="dim">${lead.updated ? 'Last saved ' + esc(lead.updated.replace('T', ' ')) : ''}</span></div>

    <h3>Rating ${r[10] > 0 ? r[10].toFixed(1) + ' from ' + fmt(r[11]) + ' reviews' : '- no reviews yet'}${r[22] >= 0 ? `<span class="dim" style="font-weight:400"> - latest review ${r[22] === 0 ? 'this month' : r[22] + ' month' + (r[22] > 1 ? 's' : '') + ' ago'}</span>` : ''}</h3>
    ${starTotal ? `<div class="bars narrow" id="p-stars"></div>
      <div class="dim" style="margin-top:4px">${r[26]}% of ratings are 1-2 star${unhappy(r) ? ' <span class="closed">- many unhappy customers</span>' : ''}</div>` : ''}

    ${ageTotal ? `<h3>When the saved reviews were written <span class="dim" style="font-weight:400">- ${ageTotal} of ${fmt(r[11])}, a sample</span></h3>
      <div class="bars narrow wide-label" id="p-ages"></div>` : ''}

    ${d.busy && d.busy.length ? `<h3>Busy hours <span class="dim" style="font-weight:400">- typical week, Google's 0-100 scale</span></h3>
      <select id="p-day">${DAY_NAMES.map((day, i) => `<option value="${i}"${i === today() ? ' selected' : ''}>${day}</option>`).join('')}</select>
      <div id="p-busy"></div><div class="dim" id="p-peak"></div>` : ''}

    ${d.hours && d.hours.length ? `<h3>Opening hours ${open === null ? '' : open ? '<span class="ok" style="font-weight:400">- open now</span>' : '<span class="dim" style="font-weight:400">- closed now</span>'}</h3>
      <table class="hours">${d.hours.map(([day, text], i) => `<tr class="${i === today() ? 'today' : ''}"><td>${day}</td><td>${esc(text || 'Closed')}</td></tr>`).join('')}</table>` : ''}

    ${d.reviews && d.reviews.length ? `<h3>Recent reviews</h3>` + d.reviews.map(v => `<div class="review"><b>${esc(v.n)}</b> <span class="dim">- ${v.r ? v.r + ' star' + (v.r === 1 ? '' : 's') : ''} ${esc(v.w)}</span>
      ${v.t ? `<div>${esc(v.t)}</div>` : '<div class="dim">(rating only, no text)</div>'}</div>`).join('') : ''}`;

  if (starTotal) bars('p-stars', [5, 4, 3, 2, 1].map(n => [n + ' star', d.stars[n - 1]]), starTotal);
  if (ageTotal) bars('p-ages', ['Last 3 months', '4 - 11 months ago', '1 - 2 years ago', 'Older'].map((label, i) => [label, d.ages[i]]), ageTotal);
  if ($('p-day')) { $('p-day').onchange = () => renderBusy(d.busy); renderBusy(d.busy); }
  $('p-close').onclick = closePlace;
  $('p-edit').onclick = () => showEditForm(r, d);
  $('p-save').onclick = () => saveLead(key);
  if ($('p-same')) $('p-same').onclick = ev => { ev.preventDefault(); resetFilters(); $('f-closed').value = 'all'; $('f-q').value = r[5]; applyFilters(); };
}

const hourLabel = h => (h % 12 || 12) + (h < 12 ? 'am' : 'pm');

// Column chart of one weekday's busy level, plus the week's peak in words.
function renderBusy(week) {
  const day = +$('p-day').value, hours = week[day].slice(FIRST_BUSY_HOUR), nowHour = new Date().getHours();
  $('p-busy').innerHTML = `<div class="cols">${hours.map((level, i) => {
    const hour = i + FIRST_BUSY_HOUR;
    return `<div class="col${day === today() && hour === nowHour ? ' now' : ''}" title="${DAY_NAMES[day]} ${hourLabel(hour)}: ${level} of 100"><div style="height:${level}%"></div></div>`;
  }).join('')}</div><div class="cols ticks">${hours.map((_, i) => `<div>${i % 3 ? '' : hourLabel(i + FIRST_BUSY_HOUR)}</div>`).join('')}</div>`;
  let peak = [0, 0, 0];
  week.forEach((levels, d) => levels.forEach((level, h) => { if (level > peak[0]) peak = [level, d, h]; }));
  $('p-peak').textContent = peak[0] ? `Busiest: ${DAY_NAMES[peak[1]]} around ${hourLabel(peak[2])}` : 'No busy data for this day';
}

// ---- correcting a place, or adding one that Google does not list ----
const EDIT_FIELDS = [['title', 'Name', 0], ['category', 'Type', 1, 'dl-category'], ['phone', 'Phone', 5], ['website', 'Website', 7],
  ['emails', 'Email', 9], ['address', 'Address', 4], ['locality', 'Area', 2, 'dl-locality'], ['pincode', 'Pincode', 3]];

// r is the place to correct, or null to add a new business.
function showEditForm(r, d) {
  openKey = r ? r[23] : '';
  $('panel').classList.add('open');
  const value = idx => r && !['(no category)', '(unknown)'].includes(r[idx]) ? r[idx] : '';
  $('panel').innerHTML = `
    <div class="head"><div class="name">${r ? 'Edit: ' + esc(r[0]) : 'Add a business'}</div><button id="p-close" aria-label="Close">Close</button></div>
    <p class="dim">${r ? 'Your changes are kept separately and survive new scrapes. Google data is not overwritten.' : 'For a business that is not on Google Maps. Only the name is required.'}</p>
    <div class="form">${EDIT_FIELDS.map(([name, label, idx, list]) =>
      `<label for="e-${name}">${label}</label><input id="e-${name}" value="${esc(value(idx))}"${list ? ` list="${list}"` : ''} autocomplete="off">`).join('')}</div>
    <div class="actions">
      <button id="e-save">${r ? 'Save changes' : 'Add business'}</button>
      <button id="e-cancel">Cancel</button>
      ${r && d && d.edited && d.edited.length ? '<button id="e-restore">Restore Google data</button>' : ''}
    </div>
    <div id="e-status" class="dim" style="margin-top:8px"></div>`;
  $('p-close').onclick = closePlace;
  $('e-cancel').onclick = () => r ? openPlace(r[23]) : closePlace();
  $('e-save').onclick = () => saveEdit(r, false);
  if ($('e-restore')) $('e-restore').onclick = () => saveEdit(r, true);
  $('e-' + (r ? 'phone' : 'title')).focus();
}

async function saveEdit(r, restore) {
  const fields = Object.fromEntries(EDIT_FIELDS.map(([name]) => [name, $('e-' + name).value.trim()]));
  if (!fields.title) { $('e-status').innerHTML = '<span class="closed">Name cannot be empty</span>'; return; }
  $('e-status').textContent = 'Saving...';
  try {
    const res = await fetch(r ? '/edit' : '/add', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(r ? {key: r[23], fields, restore} : {fields})});
    if (!res.ok) throw new Error(res.status);
    const body = await res.json();
    document.activeElement.blur();  // so the refresh below is not postponed
    if (r) {
      DATA[DATA.findIndex(x => x[23] === r[23])] = body.row;
      countPhones(); fillSelects(); filterRows(); render();
    } else {
      await load();  // the new business arrives like any freshly scraped one
    }
    openPlace(r ? r[23] : body.key);
  } catch (err) {
    $('e-status').innerHTML = '<span class="closed">Not saved - is analyze.py still running?</span>';
  }
}

function closePlace() { openKey = ''; $('panel').classList.remove('open'); }

async function saveLead(key) {
  const status = $('p-status').value, note = $('p-note').value.trim();
  $('p-saved').textContent = 'Saving...';
  try {
    const res = await fetch('/lead', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({key, status, note})});
    if (!res.ok) throw new Error(res.status);
    if (status === 'new' && !note) delete LEADS[key]; else LEADS[key] = {status, note, updated: new Date().toISOString().slice(0, 19)};
    $('p-saved').textContent = 'Saved';
    filterRows(); renderTiles(); renderCharts(); renderPlaces();
  } catch (err) {
    $('p-saved').innerHTML = '<span class="closed">Not saved - is analyze.py still running?</span>';
  }
}

function exportCsv() {
  if (!rows || rows.length === 0) {
    showToast('No places to export');
    return;
  }
  const head = ['title', 'category', 'locality', 'pincode', 'address', 'phone', 'phone_type', 'website', 'website_type', 'emails',
    'rating', 'reviews', 'latitude', 'longitude', 'claimed', 'status', 'maps_link', 'details', 'services', 'hours',
    'lead_score', 'months_since_review', 'lead_status', 'lead_note', 'business_fit', 'reviews_last_3_months', 'low_rating_percent', 'guessed_fields', 'edited_by_me'];
  const cell = v => /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : v;
  const lines = [head.join(',')].concat(rows.map(r => {
    const lead = LEADS[r[23]] || {};
    const out = r.slice(0, 21).map((v, i) =>
      i === 6 ? ({m: 'mobile', l: 'landline'}[v] || '') : i === 8 ? ({o: 'own', s: 'social/listing', n: 'none'}[v]) : i === 14 ? (v ? 'yes' : 'no') : v);
    return out.concat([r[22] >= 0 ? r[22] : '', lead.status || 'new', lead.note || '', r[24] ? 'good' : 'low', r[25], r[26] >= 0 ? r[26] : '', ['', 'category', 'locality', 'category, locality'][r[27] & 3], r[27] & 8 ? 'added' : r[27] & 4 ? 'edited' : '']).map(cell).join(',');
  }));
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['\ufeff' + lines.join('\r\n')], {type: 'text/csv'}));
  a.download = `leads_filtered_${rows.length}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
  showToast(`Exported ${fmt(rows.length)} places to CSV`);
}

const FILTER_IDS = ['f-q', 'f-cat', 'f-loc', 'f-pin', 'f-web', 'f-phone', 'f-rating', 'f-rev', 'f-score', 'f-hours', 'f-active', 'f-neg', 'f-claim', 'f-closed', 'f-fit', 'f-lead'];
const PRESETS = {
  'best': {'f-score': '70'},
  'noweb-mobile': {'f-web': 'n', 'f-phone': 'm'},
  'popular-noweb': {'f-web': 'sn', 'f-rev': '50'},
  'low-rated': {'f-rating': 'low', 'f-rev': '10'},
  'unclaimed': {'f-claim': '0'},
  'open-now': {'f-hours': 'now', 'f-phone': 'y'},
  'followup': {'f-lead': 'interested'},
  'unhappy': {'f-neg': '20'},
  'active': {'f-active': 'recent'},
};
function resetFilters() { FILTER_IDS.forEach(id => { if (id === 'f-q') $(id).value = ''; else $(id).selectedIndex = 0; }); }

// Poll the server; it only sends the places this page does not hold yet.
async function load() {
  try {
    const res = await fetch(`/data.json?epoch=${epoch}&have=${DATA.length}`);
    if (!res.ok) throw new Error(res.status);
    const body = await res.json();
    if (body.reset || body.places.length) {
      DATA = body.reset ? body.places : DATA.concat(body.places);
      if (!body.reset) lastAdded = `+${fmt(body.places.length)} at ${new Date().toLocaleTimeString()}`;
      epoch = body.epoch;
      countPhones();
      showNewData();
    }
    updateScrapeBar(body.scrape);
    $('total').textContent = fmt(DATA.length) + ' businesses' + (lastAdded ? ` (${lastAdded})` : '');
    $('live').textContent = 'live, checked ' + new Date().toLocaleTimeString() + ', file changed ' + body.modified;
    $('live').className = 'ok';
  } catch (err) {
    $('live').textContent = 'not connected - start Lead Finder again';
    $('live').className = 'closed';
  }
}

$('f-lead').innerHTML = '<option value="">Lead status: any</option>' + Object.keys(LEAD_NAMES).map(k => `<option value="${k}">${LEAD_NAMES[k]}</option>`).join('');
FILTER_IDS.filter(id => id !== 'f-q').forEach(id => $(id).addEventListener('change', ev => {
  ev.target.blur();  // let background refreshes resume
  applyFilters();
}));
let searchTimer = 0;
const qInput = $('f-q');
const qClear = $('f-q-clear');
if (qInput) {
  qInput.addEventListener('input', () => {
    if (qClear) qClear.style.display = qInput.value ? 'block' : 'none';
    clearTimeout(searchTimer);
    searchTimer = setTimeout(applyFilters, SEARCH_DELAY_MS);
  });
  if (qClear) {
    qClear.onclick = () => {
      qInput.value = '';
      qClear.style.display = 'none';
      applyFilters();
      qInput.focus();
    };
  }
}
// Keyboard shortcuts for interactive navigation
document.addEventListener('keydown', ev => {
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes((document.activeElement || {}).tagName)) {
    if (ev.key === 'Escape') document.activeElement.blur();
    return;
  }
  if (ev.key === 'ArrowLeft' || ev.key === '[') {
    if (page > 0) { page--; renderPlaces(); }
  } else if (ev.key === 'ArrowRight' || ev.key === ']') {
    const pageSize = PAGE_SIZE === -1 ? (rows.length || 1) : PAGE_SIZE;
    const pages = Math.max(1, Math.ceil(rows.length / pageSize));
    if (page < pages - 1) { page++; renderPlaces(); }
  } else if (ev.key === '/') {
    ev.preventDefault();
    if (qInput) { qInput.focus(); qInput.select(); }
  } else if (ev.key === 'Escape') {
    if (openKey) closePlace();
  }
});

// Show places that arrived while the user was typing or had a dropdown open.
document.addEventListener('focusout', () => setTimeout(() => { if (dirty) showNewData(); }, 0));
$('reset').onclick = () => {
  resetFilters();
  if (qClear) qClear.style.display = 'none';
  applyFilters();
};
document.querySelectorAll('[data-preset]').forEach(b => b.onclick = () => {
  resetFilters();
  Object.entries(PRESETS[b.dataset.preset]).forEach(([id, v]) => $(id).value = v);
  if (qClear) qClear.style.display = (qInput && qInput.value) ? 'block' : 'none';
  applyFilters();
});

// ---- simple view: a few filters by default, the rest behind two buttons ----

function setSimple(simple) {
  document.body.classList.toggle('simple', simple);
  $('more').textContent = simple ? 'More filters' : 'Fewer filters';
  remember('lf-simple', simple ? '1' : '0');
}
function setInsights(shown) {
  document.body.classList.toggle('show-insights', shown);
  $('insights').textContent = shown ? 'Hide insights' : 'Show insights';
  remember('lf-insights', shown ? '1' : '0');
}
$('more').onclick = () => {
  const simple = !document.body.classList.contains('simple');
  setSimple(simple);
  // A hidden filter that still narrows the list would be confusing: clear them.
  if (simple) { ADVANCED_FILTERS.forEach(id => { $(id).selectedIndex = 0; }); applyFilters(); }
};
$('insights').onclick = () => {
  setInsights(!insightsShown());
  if (insightsShown()) { render(); if (map) map.invalidateSize(); }
};
setSimple(recall('lf-simple') !== '0');
setInsights(recall('lf-insights') === '1');

// ---- finding businesses: start, watch and stop the Google Maps search ----
let scrapeStatus = null, scrapeTimer = 0;

function scrapeProgress(s) {
  if (!s.total) return s.done ? `${fmt(s.done)} searches done` : '';
  return `${fmt(Math.min(s.done, s.total))} of about ${fmt(s.total)} searches done (${Math.min(100, Math.round(100 * s.done / s.total))}%)`;
}

function updateScrapeBar(s) {
  if (!s) return;
  scrapeStatus = s;
  const active = s.state === 'running' || s.state === 'waiting', area = s.area || 'your area';
  const text = !s.exe_found ? 'The scraper program (google-maps-scraper.exe) is missing from the app folder.'
    : s.state === 'running' ? `Searching Google Maps in ${area}: ${scrapeProgress(s) || 'starting...'}`
    : s.state === 'waiting' ? `${s.message}. ${scrapeProgress(s)}`
    : s.state === 'complete' ? `Search of ${area} finished. ${scrapeProgress(s)}`
    : s.state === 'blocked' || s.state === 'error' ? s.message
    : s.done ? `Search of ${area} is paused. ${scrapeProgress(s)}`
    : 'No search running. Press "Find businesses" to collect leads from Google Maps.';
  $('scrape-text').textContent = text;
  $('scrape-stop').hidden = !active;
  $('scrape-continue').hidden = active || !s.area || s.state === 'complete' || !s.exe_found;
  if ($('s-status')) renderScrapeStatus();
}

async function scrapePost(path, body) {
  const res = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body || {})});
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Could not reach Lead Finder');
  updateScrapeBar(data);
}

$('scrape-stop').onclick = () => scrapePost('/scrape/stop').catch(err => { $('scrape-text').textContent = err.message; });
$('scrape-continue').onclick = () => scrapePost('/scrape/start').catch(err => { $('scrape-text').textContent = err.message; });
$('scrape-open').onclick = openScrapePanel;

function renderScrapeStatus() {
  const s = scrapeStatus, active = s.state === 'running' || s.state === 'waiting';
  $('s-status').innerHTML = `<b>${active ? 'Search is running' : s.state === 'complete' ? 'Search finished' : 'Search is not running'}</b>
    ${s.total ? `<div class="progress"><div style="width:${Math.min(100, 100 * s.done / s.total)}%"></div></div>` : ''}
    <div class="dim">${esc(scrapeProgress(s))}${s.errors ? ` - ${fmt(s.errors)} failed page loads this round` : ''}</div>
    <div class="dim">${esc(s.message || '')}</div>`;
  $('s-start').textContent = active ? 'Stop search' : 'Start search';
  $('s-form').style.opacity = active ? .5 : 1;
  $('s-form').querySelectorAll('input,select,textarea,button').forEach(el => { el.disabled = active; });
}

async function openScrapePanel() {
  openKey = '';
  $('panel').classList.add('open');
  $('panel').innerHTML = '<div class="head"><div class="name">Find businesses</div><button id="p-close">Close</button></div><p class="dim">Loading...</p>';
  $('p-close').onclick = closePlace;
  let s;
  try { s = await (await fetch('/scrape.json')).json(); } catch (err) { $('panel').querySelector('p').textContent = 'Could not reach Lead Finder.'; return; }
  scrapeStatus = s;
  const saved = s.settings || {}, keywords = (saved.keywords && saved.keywords.length ? saved.keywords : s.recommended).join('\n');
  let bbox = saved.bbox || null, areaMap = null, rectangle = null;

  $('panel').innerHTML = `
    <div class="head"><div class="name">Find businesses</div><button id="p-close" aria-label="Close">Close</button></div>
    <p class="dim">Lead Finder searches Google Maps square by square across the area, once for every business type, and adds what it finds to your list.</p>
    <div id="s-status" class="card" style="margin-top:10px"></div>
    <div id="s-form">
      <h3>1. Where</h3>
      <div style="display:flex;gap:8px"><input id="s-area" style="flex:1" placeholder="City or area, e.g. Jodhpur" value="${esc(saved.area || '')}"><button id="s-find">Find on map</button></div>
      <div id="s-map"></div>
      <div class="dim" id="s-size"></div>
      <h3>2. Which businesses <span class="dim" style="font-weight:400" id="s-count"></span></h3>
      <div class="form" style="grid-template-columns:1fr"><textarea id="s-keywords" spellcheck="false">${esc(keywords)}</textarea></div>
      <div class="actions" style="margin-top:6px"><button id="s-recommended">Use recommended list</button><span class="dim">one business type per line</span></div>
      <h3>3. How</h3>
      <div class="form">
        <label for="s-cell">Detail</label><select id="s-cell">
          <option value="2">Standard - 2 km squares</option><option value="1">Thorough - 1 km squares, 4x longer</option><option value="3">Quick - 3 km squares, may miss some</option></select>
        <label for="s-speed">Speed</label><select id="s-speed">
          <option value="8">Normal</option><option value="4">Gentle - PC stays fast, lower block risk</option><option value="12">Fast - heavy on the PC</option></select>
      </div>
      <div class="dim" id="s-estimate" style="margin-top:8px"></div>
    </div>
    <div class="actions"><button id="s-start" class="primary">Start search</button></div>
    <div id="s-error" class="closed" style="margin-top:8px"></div>
    <p class="dim" style="margin-top:14px">Keep the Lead Finder window open while it searches. You can close this panel and keep working; new leads appear on their own.
      Stopping is safe: next time it continues where it left off.</p>`;
  $('p-close').onclick = closePlace;
  $('s-cell').value = String(saved.cell || 2);
  $('s-speed').value = String(saved.concurrency || 8);

  const keywordList = () => $('s-keywords').value.split('\n').map(k => k.trim()).filter(Boolean);
  function describe() {
    const count = keywordList().length;
    $('s-count').textContent = `- ${count} type${count === 1 ? '' : 's'}`;
    if (!bbox) { $('s-size').textContent = 'No area chosen yet.'; $('s-estimate').textContent = ''; return; }
    const cell = +$('s-cell').value;
    const heightKm = (bbox[2] - bbox[0]) * KM_PER_DEGREE, widthKm = (bbox[3] - bbox[1]) * KM_PER_DEGREE * Math.cos(bbox[0] * Math.PI / 180);
    const searches = Math.ceil(heightKm / cell) * Math.ceil(widthKm / cell) * count;
    // Speed scales with the number of parallel browsers; 25 a minute was measured at "Normal".
    const hours = searches / (SEARCHES_PER_MINUTE * +$('s-speed').value / 8) / 60;
    $('s-size').textContent = `Area: about ${Math.round(widthKm)} x ${Math.round(heightKm)} km`;
    $('s-estimate').textContent = `About ${fmt(searches)} searches - roughly ${hours < 1 ? 'under an hour' : Math.round(hours) + ' hour' + (Math.round(hours) === 1 ? '' : 's')}.`;
  }
  function showArea() {
    if (!window.L) { $('s-map').style.display = 'none'; return; }
    if (!areaMap) {
      areaMap = L.map('s-map');
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'}).addTo(areaMap);
      areaMap.setView([22.5, 79], 4);
    }
    if (rectangle) rectangle.remove();
    if (bbox) {
      const bounds = [[bbox[0], bbox[1]], [bbox[2], bbox[3]]];
      rectangle = L.rectangle(bounds, {color: getComputedStyle(document.documentElement).getPropertyValue('--bar').trim(), weight: 2, fillOpacity: .08}).addTo(areaMap);
      areaMap.fitBounds(bounds, {padding: [12, 12]});
    }
  }
  async function findArea() {
    const name = $('s-area').value.trim();
    if (!name) { $('s-error').textContent = 'Type a city or area name first.'; return; }
    $('s-error').textContent = '';
    $('s-size').textContent = 'Looking up the area...';
    try {
      const found = await (await fetch('https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(name))).json();
      if (!found.length) throw new Error('not found');
      const [south, north, west, east] = found[0].boundingbox.map(Number);
      bbox = [south, west, north, east];
      showArea(); describe();
    } catch (err) {
      $('s-size').textContent = '';
      $('s-error').textContent = 'Could not find that place. Check the spelling and your internet connection.';
    }
  }

  $('s-find').onclick = findArea;
  $('s-area').onkeydown = ev => { if (ev.key === 'Enter') findArea(); };
  $('s-recommended').onclick = () => { $('s-keywords').value = s.recommended.join('\n'); describe(); };
  ['s-keywords', 's-cell', 's-speed'].forEach(id => $(id).addEventListener('input', describe));
  $('s-start').onclick = async () => {
    $('s-error').textContent = '';
    const active = scrapeStatus.state === 'running' || scrapeStatus.state === 'waiting';
    try {
      if (active) await scrapePost('/scrape/stop');
      else await scrapePost('/scrape/start', {settings: {area: $('s-area').value.trim(), bbox, keywords: keywordList(), cell: +$('s-cell').value, concurrency: +$('s-speed').value}});
    } catch (err) { $('s-error').textContent = err.message; }
  };

  showArea(); describe(); renderScrapeStatus();
  clearInterval(scrapeTimer);
  // Faster progress updates while this panel is open.
  scrapeTimer = setInterval(async () => {
    if (!$('s-status')) { clearInterval(scrapeTimer); return; }
    try { const res = await fetch('/scrape.json'); if (res.ok) updateScrapeBar(await res.json()); } catch (err) { /* next tick retries */ }
  }, SCRAPE_POLL_MS);
}
$('add-place').onclick = () => showEditForm(null, null);
document.addEventListener('click', ev => {
  const el = ev.target.closest('[data-open]');
  if (el) { ev.preventDefault(); openPlace(el.dataset.open); }
});
document.addEventListener('keydown', ev => { if (ev.key === 'Escape') closePlace(); });

fetch('/leads.json').then(res => res.json()).then(leads => { LEADS = leads; }).catch(() => {}).finally(() => {
  load();
  setInterval(load, REFRESH_MS);
});
</script>
</body>
</html>
"""


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
