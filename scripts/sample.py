#!/usr/bin/env python3
"""Seeded, reproducible draw of Karnataka public authorities for RTI filing.

Adapted from the Tamil Nadu design in in-rolls/rti (batch b2026q3_02):
one Random(seed) drives every draw, rows are sorted by a stable key before
any randomness touches them, and the batch metadata freezes the seed, the
config, and the SHA-256 of the frame so the draw reproduces exactly.

The Karnataka frame is a flat portal-derived roster (no department/tier
tree), so stratification is by department group, derived from the office
name's trailing segment.
Allocation is proportional to stratum size (largest remainder), with a cap
per stratum so no single department dominates, and a floor of one for every
stratum large enough to be sampled at all. Treatment (plain vs legal
salience, 70/30) and RA assignment (RA1-RA4) are balanced within strata.

Usage: python3 scripts/sample.py   (from the repository root)
"""

import csv
import hashlib
import json
import random
import re
from pathlib import Path

# ---- frozen batch design -------------------------------------------------
BATCH_ID = "ka2026q3_01"
SEED = 20260907          # new batch, new seed; never reuse a seed
N = 180                  # 10% of the 1,800-office universe
N_LEGAL = 54             # 30% legal salience
N_PLAIN = 126            # 70% plain
RAS = ["RA1", "RA2", "RA3", "RA4"]
CAP_PER_STRATUM = 15     # no department group takes more than this
MIN_STRATUM_SIZE = 5     # smaller groups are pooled into OTHER
ID_PREFIX = "KA"
STATE = "Karnataka"

ROOT = Path(__file__).resolve().parent.parent
FRAME = ROOT / "data" / "frame_karnataka.csv"
OUT = ROOT / "out" / BATCH_ID


def dept_group(name: str) -> str:
    """Department group from an office name. Karnataka names lead with the
    district and end with the department ('Bagalkot-Mudhol-Rural Development
    and Panchayat Raj - RDPR(T.P)'), so the group is the LAST '-'-separated
    segment; when that segment itself carries an office prefix before a comma
    ('Office of The Deputy Director,Department of Mines and Geology'), the
    part after the last comma wins. Lightly normalised so variants land
    together."""
    g = name.split("-")[-1]
    if "," in g:
        g = g.split(",")[-1]
    g = g.upper().replace("&", " AND ")
    g = re.sub(r"\s+", " ", g).strip(" .-")
    g = re.sub(r"^(DEPARTMENT|DEPT\.?)\s+OF\s+", "", g)
    g = re.sub(r"\s+(DEPARTMENT|DEPT\.?)$", "", g)
    return g or "UNKNOWN"


def largest_remainder(weights, total, caps, floors):
    """Integer allocation of `total` across strata proportional to weights,
    respecting per-stratum caps and floors. Deterministic."""
    keys = sorted(weights)
    alloc = {k: floors[k] for k in keys}
    remaining = total - sum(alloc.values())
    if remaining < 0:
        raise SystemExit("floors exceed total")
    while remaining > 0:
        # ideal share of what is left, among strata with headroom
        open_keys = [k for k in keys if alloc[k] < caps[k]]
        if not open_keys:
            raise SystemExit("caps too tight for requested n")
        wsum = sum(weights[k] for k in open_keys)
        quotas = {k: remaining * weights[k] / wsum for k in open_keys}
        gave = 0
        for k in sorted(open_keys, key=lambda k: (-quotas[k], k)):
            if remaining - gave == 0:
                break
            take = min(int(quotas[k]) or 1, caps[k] - alloc[k], remaining - gave)
            alloc[k] += take
            gave += take
        if gave == 0:  # all integer parts zero: hand out singles
            for k in sorted(open_keys, key=lambda k: (-quotas[k], k)):
                if remaining - gave == 0:
                    break
                alloc[k] += 1
                gave += 1
        remaining -= gave
    return alloc


def main():
    rows = list(csv.DictReader(open(FRAME, encoding="utf-8")))
    frame_sha = hashlib.sha256(FRAME.read_bytes()).hexdigest()
    for r in rows:
        r["dept_group"] = dept_group(r["Department"])

    # stable order before any randomness
    rows.sort(key=lambda r: (r["dept_group"], r["Department"], r["District"]))

    strata = {}
    for r in rows:
        strata.setdefault(r["dept_group"], []).append(r)
    # pool small groups
    pooled = {}
    for g, members in strata.items():
        key = g if len(members) >= MIN_STRATUM_SIZE else "OTHER (POOLED SMALL OFFICES)"
        pooled.setdefault(key, []).extend(members)
    strata = pooled

    weights = {g: len(m) for g, m in strata.items()}
    caps = {g: min(CAP_PER_STRATUM, len(m)) for g, m in strata.items()}
    floors = {g: 1 for g in strata}
    if sum(floors.values()) > N:
        raise SystemExit("more strata than sample slots; raise N or MIN_STRATUM_SIZE")
    alloc = largest_remainder(weights, N, caps, floors)

    rng = random.Random(SEED)
    sampled = []
    for g in sorted(strata):
        members = strata[g]
        take = alloc[g]
        sampled.extend(rng.sample(members, take))

    # treatment: 70/30 within stratum, then trimmed to exact global counts
    sampled.sort(key=lambda r: (r["dept_group"], r["Department"], r["District"]))
    rng.shuffle(sampled)
    for i, r in enumerate(sampled):
        r["treatment"] = "legal_salience" if i < N_LEGAL else "plain"
    # RA assignment balanced within treatment; the round-robin for the second
    # arm continues where the first left off, so overall loads stay even
    offset = 0
    for arm in ("legal_salience", "plain"):
        arm_rows = [r for r in sampled if r["treatment"] == arm]
        rng.shuffle(arm_rows)
        for i, r in enumerate(arm_rows):
            r["assigned_ra"] = RAS[(offset + i) % len(RAS)]
        offset = (offset + len(arm_rows)) % len(RAS)

    sampled.sort(key=lambda r: (r["dept_group"], r["Department"], r["District"]))
    for i, r in enumerate(sampled, start=1):
        r["application_id"] = f"{ID_PREFIX}-{i:03d}"

    fields = [
        "application_id", "batch_id", "state", "office_name", "dept_group",
        "district", "block", "portal", "treatment", "assigned_ra",
        "template_version", "language", "channel",
        "filing_outcome", "not_filed_reason", "filing_date",
        "registration_number", "fee_paid_inr", "payment_mode",
        "payment_reference", "due_date", "notes",
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "worklists").mkdir(exist_ok=True)

    def to_row(r):
        return {
            "application_id": r["application_id"], "batch_id": BATCH_ID,
            "state": STATE, "office_name": r["Department"],
            "dept_group": r["dept_group"], "district": r["District"],
            "block": r["Block"], "portal": r["Website"],
            "treatment": r["treatment"], "assigned_ra": r["assigned_ra"],
            "template_version": "v3", "language": "en", "channel": "portal",
            "filing_outcome": "", "not_filed_reason": "", "filing_date": "",
            "registration_number": "", "fee_paid_inr": "", "payment_mode": "",
            "payment_reference": "", "due_date": "", "notes": "",
        }

    with open(OUT / "assignments.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in sampled:
            w.writerow(to_row(r))

    for ra in RAS:
        with open(OUT / "worklists" / f"{ra}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in sampled:
                if r["assigned_ra"] == ra:
                    w.writerow(to_row(r))

    meta = {
        "batch_id": BATCH_ID, "seed": SEED, "state": STATE, "n": N,
        "n_legal": N_LEGAL, "n_plain": N_PLAIN, "ras": RAS,
        "cap_per_stratum": CAP_PER_STRATUM, "min_stratum_size": MIN_STRATUM_SIZE,
        "frame_csv": str(FRAME.relative_to(ROOT)), "frame_sha256": frame_sha,
        "frame_rows": len(rows), "strata": len(strata),
        "source": "portal scrape 2026-09-07 via scripts/build_frame.py (see data/frame_provenance.json)",
        "templates": {"plain": "plain_v3.txt", "legal_salience": "legal_salience_v3.txt"},
    }
    with open(OUT / "batch_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    per_ra = {ra: sum(1 for r in sampled if r["assigned_ra"] == ra) for ra in RAS}
    print(f"{len(sampled)} sampled | strata {len(strata)} | "
          f"legal {sum(1 for r in sampled if r['treatment']=='legal_salience')} "
          f"plain {sum(1 for r in sampled if r['treatment']=='plain')} | {per_ra}")


if __name__ == "__main__":
    main()
