"""Build app/zip_centers.csv: public ZIP code -> city and center point.

Run once (needs internet), then commit the CSV. After that the app looks ZIPs up
offline, so a ZIP a customer types is never sent to a third party.

    python -m scripts.build_zip_table
"""
import csv
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "app" / "zip_centers.csv"
RANGE = range(24000, 24700)  # southwest Virginia, covers the Roanoke service area


def fetch(zip_code: int):
    z = f"{zip_code:05d}"
    try:
        with urllib.request.urlopen(f"https://api.zippopotam.us/us/{z}", timeout=15) as r:
            p = json.load(r)["places"][0]
        return z, p["place name"], p["state abbreviation"], round(float(p["latitude"]), 3), round(float(p["longitude"]), 3)
    except (urllib.error.HTTPError, urllib.error.URLError, KeyError, TimeoutError):
        return None


if __name__ == "__main__":
    with ThreadPoolExecutor(8) as pool:
        rows = [r for r in pool.map(fetch, RANGE) if r and r[2] == "VA"]
    OUT.parent.mkdir(exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["zip", "city", "state", "lat", "lon"])
        w.writerows(sorted(rows))
    print(f"wrote {len(rows)} ZIP codes to {OUT}")
