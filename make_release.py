"""Build the customer download: one zip that runs with nothing else installed.

Usage:
    python make_release.py [--version 1.0] [--go path\\to\\go.exe]

Writes dist/LeadFinder-v<version>.zip containing the app, a freshly built
scraper and its own copy of Python, so the customer only unzips and
double-clicks "Start Lead Finder.bat".
"""

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"

# Official "embeddable" Python for 64-bit Windows: no installer, just files.
PYTHON_VERSION = "3.12.10"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
# Pinned so a tampered or swapped download is refused.
PYTHON_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"

APP_FILES = ("Start Lead Finder.bat", "analyze.py", "scraper_control.py")
SCRAPER_EXE = "google-maps-scraper.exe"
# -s -w drop debug symbols and -trimpath drops this PC's folder names: 31 MB -> 22 MB.
BUILD_FLAGS = ("-trimpath", "-ldflags", "-s -w")

README_TEXT = """Lead Finder {version}

HOW TO START
  1. Unzip this folder anywhere (for example your Desktop).
  2. Double-click "Start Lead Finder.bat".
  3. Your browser opens the app. Keep the black window open while you work.
  4. Press "Find businesses", type a city, press "Find on map", then "Start search".

The first search downloads the browser it needs (a few hundred MB), so it
needs internet and takes a few minutes before leads start to appear. Later
searches start right away. Windows may ask to allow the app the first time -
choose "More info" then "Run anyway".

YOUR DATA
  Everything you collect is saved in the "data" folder next to this file.
  Copy that folder to back up your leads, notes and corrections.

UPDATING
  Unzip the new version, then copy your old "data" folder into it.

Nothing is installed on your PC. To remove Lead Finder, delete this folder.
"""


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build_scraper(go, target):
    env = {**os.environ, "GOOS": "windows", "GOARCH": "amd64", "CGO_ENABLED": "0"}
    subprocess.run([go, "build", *BUILD_FLAGS, "-o", str(target), "."], cwd=ROOT, env=env, check=True)


def fetch_python(cache):
    """Download the embeddable Python once and check it is the file we expect."""
    archive = cache / Path(PYTHON_URL).name
    if not archive.exists():
        print(f"downloading {PYTHON_URL}")
        with urllib.request.urlopen(PYTHON_URL) as response, open(archive, "wb") as out:  # noqa: S310 - fixed https URL
            shutil.copyfileobj(response, out)
    actual = sha256(archive)
    if PYTHON_SHA256 and actual != PYTHON_SHA256:
        archive.unlink()
        sys.exit(f"Python download does not match the pinned checksum.\n  expected {PYTHON_SHA256}\n  got      {actual}")
    if not PYTHON_SHA256:
        print(f"WARNING: no checksum pinned. Set PYTHON_SHA256 = \"{actual}\" in make_release.py")
    return archive


def add_python(archive, target):
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(target)
    # The embeddable Python only imports from the folders listed in its ._pth file.
    # Adding ".." lets it find analyze.py and scraper_control.py one level up.
    for pth in target.glob("python*._pth"):
        lines = pth.read_text(encoding="utf-8").splitlines()
        if ".." not in lines:
            lines.insert(lines.index(".") + 1 if "." in lines else 0, "..")
        pth.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Build the Lead Finder download zip.")
    parser.add_argument("--version", default="1.0")
    parser.add_argument("--go", default=shutil.which("go") or "go", help="path to the Go compiler")
    args = parser.parse_args()

    if not re.match(r"^[a-zA-Z0-9._-]+$", args.version):
        sys.exit(f"Invalid version string: '{args.version}'. Use alphanumeric characters, dots, and hyphens only.")

    name = f"LeadFinder-v{args.version}"
    stage = DIST / name
    cache = DIST / "cache"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    cache.mkdir(exist_ok=True)

    print("building the scraper")
    build_scraper(args.go, stage / SCRAPER_EXE)

    for file in APP_FILES:
        shutil.copy2(ROOT / file, stage / file)
    add_python(fetch_python(cache), stage / "python")

    (stage / "README.txt").write_text(README_TEXT.format(version=args.version), encoding="utf-8")
    licenses = stage / "licenses"
    licenses.mkdir()
    shutil.copy2(ROOT / "LICENSE", licenses / "google-maps-scraper-LICENSE.txt")
    shutil.copy2(ROOT / "third_party" / "scrapemate" / "LICENSE", licenses / "scrapemate-LICENSE.txt")

    zip_path = DIST / f"{name}.zip"
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                bundle.write(path, Path(name) / path.relative_to(stage))

    print(f"\n{zip_path}  {zip_path.stat().st_size / 1e6:.1f} MB")
    print(f"sha256 {sha256(zip_path)}")


if __name__ == "__main__":
    main()
