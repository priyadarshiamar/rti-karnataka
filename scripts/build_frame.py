#!/usr/bin/env python3
"""Build data/frame_karnataka.csv from the raw portal scrape.

Karnataka's RTI portal (rtionline.karnataka.gov.in) gates its filing form
behind email verification, OTP, and CAPTCHA, but publishes the complete
roster of onboarded public authorities on an unauthenticated page,
NodalOfficerDetails.php — one table row per authority. The raw HTML is
committed unmodified under data/raw/portal_scrape_<date>/; this script
parses the authority-name column, dedupes, joins district and block from
the project's earlier state_department classification where an office
matches, and writes the frame plus a provenance record.

Names are kept portal-verbatim (whitespace normalised only) because RAs
must match them against the portal's Public Authority dropdown.

Usage: python3 scripts/build_frame.py   (from the repository root)
"""

import csv
import hashlib
import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = sorted((ROOT / "data" / "raw").glob("portal_scrape_*"))[-1]
RAW_HTML = RAW_DIR / "NodalOfficerDetails.html"
OUT = ROOT / "data" / "frame_karnataka.csv"
PROV = ROOT / "data" / "frame_provenance.json"
LEGACY = ROOT / "data" / "raw" / "state_department_karnataka_legacy.csv"


def squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def fuzzy(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def district_guess(name: str) -> str:
    """Most Karnataka names lead with the district: 'Bagalkot - ...' or
    'Mysuru-...'. Used only when the legacy file has no match."""
    head = re.split(r"\s*-\s*", name)[0].strip()
    return head if 0 < len(head) <= 30 and "," not in head else ""


def main():
    html = RAW_HTML.read_text(encoding="utf-8", errors="replace")
    names = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S)[1:]:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)
        if len(cells) >= 2:
            n = squash(re.sub(r"<[^>]+>", " ", cells[1]))
            if n:
                names.append(n)
    raw_count = len(names)
    names = sorted(set(names))

    legacy = {}
    if LEGACY.exists():
        for r in csv.DictReader(open(LEGACY, encoding="utf-8")):
            legacy[fuzzy(r["Department"])] = r

    matched = 0
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["State", "Department", "Website",
                                          "District", "Block"])
        w.writeheader()
        for n in names:
            old = legacy.get(fuzzy(n))
            if old:
                matched += 1
            w.writerow({
                "State": "Karnataka", "Department": n,
                "Website": "RTI_Online_Karnataka",
                "District": old["District"] if old else district_guess(n),
                "Block": old["Block"] if old else "",
            })

    prov = {
        "scraped_on": RAW_DIR.name.replace("portal_scrape_", ""),
        "built_on": str(date.today()),
        "source_page": "https://rtionline.karnataka.gov.in/NodalOfficerDetails.php",
        "raw_rows": raw_count,
        "unique_offices": len(names),
        "district_block_joined_from_legacy_file": matched,
        "frame_sha256": hashlib.sha256(OUT.read_bytes()).hexdigest(),
        "notes": [
            "names kept portal-verbatim after whitespace normalisation",
            "NodalOfficerDetails.php is the portal's public roster of onboarded "
            "public authorities; the filing dropdown itself sits behind "
            "email+OTP+CAPTCHA and was not accessed",
            "district falls back to the leading segment of the office name "
            "when the legacy classification file has no match",
        ],
    }
    PROV.write_text(json.dumps(prov, indent=2))
    print(f"{len(names)} offices ({matched} with legacy district) -> {OUT.name}")


if __name__ == "__main__":
    main()
