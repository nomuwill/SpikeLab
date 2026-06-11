"""
Re-derive responder-feature profile from the STABLE-CORE set of responder
units, not the first-appearance set.

Method:
  1. Load all analysis.json files cycles 1-7.
  2. For each unit, count appearances as responder across ALL conditions
     and ALL cycles.
  3. Stable-core = units appearing as responder in >= 3 distinct
     (cycle, condition) measurements (or >=2 -- adjust based on coverage).
  4. Recompute responder-feature centroid from stable-core unit_features.
  5. Re-rank untested primary electrodes by Mahalanobis distance.
"""

from __future__ import annotations
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

PLAN = Path("/home/sharf-lab/Desktop/Research_automation/orchestrator/stim-optimize-maxone_2026-04-20")
WELL = PLAN / "well_0"

# 1. Count responder-appearances across ALL conditions and cycles
appearance_count = Counter()
appearance_detail = defaultdict(list)  # uid -> [(cycle, label, n_resp)]
for K in range(1, 8):
    aj = WELL / f"cycle_{K}/analysis.json"
    if not aj.exists():
        continue
    a = json.load(open(aj))
    for c in a["conditions"]:
        for uid in c.get("responsive_unit_ids", []):
            uid = int(uid)
            appearance_count[uid] += 1
            appearance_detail[uid].append((K, c["label"]))

print(f"Total distinct responder units ever observed: {len(appearance_count)}")
print(f"\nDistribution of appearance counts:")
hist = Counter(appearance_count.values())
for k in sorted(hist.keys(), reverse=True):
    print(f"  {k} appearances: {hist[k]} units")

# 2. Define stable-core thresholds
# Try several thresholds to find a sensible cut
print(f"\n=== Stable-core candidates by appearance threshold ===")
for thr in [10, 8, 6, 5, 4, 3, 2]:
    units_at = [u for u, c in appearance_count.items() if c >= thr]
    print(f"  >= {thr} appearances: {len(units_at)} units = {sorted(units_at)[:20]}")

# 3. Pick the threshold that gives ~5-15 stable-core units
# >= 3 looks like the natural cut point based on the distribution
THR = 3
stable_core = sorted([u for u, c in appearance_count.items() if c >= THR])
print(f"\n=== Stable-core (>= {THR} appearances): {len(stable_core)} units ===")
print(f"  IDs: {stable_core}")

# Show the appearance detail for each stable-core unit
print(f"\n=== Per-unit appearance history (stable-core only) ===")
for uid in stable_core:
    appearances = appearance_detail[uid]
    n = len(appearances)
    cycles_seen = sorted(set(c for c, _ in appearances))
    distinct_labels = sorted(set(L for _, L in appearances))
    print(f"  unit {uid} ({n} appearances): cycles={cycles_seen}; labels={distinct_labels[:3]}{'...' if len(distinct_labels)>3 else ''}")

# 4. Recompute responder-feature centroid from stable-core
bi = json.load(open(WELL / "baseline_info.json"))
units = bi["unit_features"]
unit_by_id = {u["unit_id"]: u for u in units}

print(f"\n=== Stable-core intrinsic features ===")
print(f"{'uid':>5} {'prim_elec':>10} {'rate_hz':>10} {'burst_part':>11} {'corr_top10':>11} {'isi_cv':>8} {'fp':>5}")
stable_features = []
for uid in stable_core:
    if uid not in unit_by_id:
        print(f"  unit {uid}: NOT in baseline_info.unit_features!")
        continue
    u = unit_by_id[uid]
    stable_features.append(u)
    print(f"  {uid:>3} {u['primary_electrode_id']:>10} {u['baseline_rate_hz']:>10.2f} "
          f"{u['burst_participation']:>11.3f} {u['mean_correlation_top10']:>11.3f} "
          f"{u['isi_cv']:>8.2f} {u['footprint_extent']:>5}")

print(f"\n=== Centroid (median) of stable-core features ===")
def centroid(features, key):
    vals = [u[key] for u in features]
    return float(np.median(vals)), float(np.percentile(vals, 25)), float(np.percentile(vals, 75))

CENTROID = {}
for k in ["baseline_rate_hz", "mean_correlation_top10", "isi_cv", "burst_participation", "footprint_extent"]:
    med, p25, p75 = centroid(stable_features, k)
    pop_med = float(np.median([u[k] for u in units]))
    CENTROID[k] = med
    print(f"  {k:>22}: stable_median={med:.3f} (p25-p75: {p25:.3f}-{p75:.3f}) | population_median={pop_med:.3f}")

# Compare to the FIRST-APPEARANCE centroid (the one I used earlier)
FIRST_APPEARANCE = [481, 522, 558, 589, 624]
fa_features = [unit_by_id[u] for u in FIRST_APPEARANCE if u in unit_by_id]
print(f"\n=== Centroid comparison: first-appearance (n={len(fa_features)}) vs stable-core (n={len(stable_features)}) ===")
for k in ["baseline_rate_hz", "mean_correlation_top10", "isi_cv", "footprint_extent"]:
    fa = float(np.median([u[k] for u in fa_features]))
    sc = float(np.median([u[k] for u in stable_features]))
    print(f"  {k:>22}: first-appearance={fa:.3f} | stable-core={sc:.3f} | diff={sc-fa:+.3f}")

# Overlap check (verify reviewer's claim)
overlap = set(stable_core) & set(FIRST_APPEARANCE)
print(f"\nOverlap between stable-core and first-appearance: {sorted(overlap)}")

# 5. Re-rank untested primary electrodes by responder-similarity
TESTED = {17216, 8468, 13244, 4024, 7094, 10684, 2266, 22522, 5320,
          22528, 11510, 19866, 4452, 16410, 20730, 16860, 1016, 4453, 1677,
          8440, 7778, 1897, 1879, 17437, 9784, 8249, 1825}

# Use SD from population for normalization
all_corr = np.array([u["mean_correlation_top10"] for u in units])
all_isi = np.array([u["isi_cv"] for u in units])
all_rate = np.array([u["baseline_rate_hz"] for u in units])
SD = {"corr": float(all_corr.std()), "isi_cv": float(all_isi.std()), "rate": float(all_rate.std())}

# Two flavors:
#   (a) Mahalanobis on (corr, isi_cv) only (matches my previous criterion)
#   (b) Same + rate
unit_by_primary = defaultdict(list)
for u in units:
    unit_by_primary[u["primary_electrode_id"]].append(u)

def score_a(u):
    return ((u["mean_correlation_top10"] - CENTROID["mean_correlation_top10"]) / SD["corr"]) ** 2 \
         + ((u["isi_cv"] - CENTROID["isi_cv"]) / SD["isi_cv"]) ** 2

def score_b(u):
    return score_a(u) + ((u["baseline_rate_hz"] - CENTROID["baseline_rate_hz"]) / SD["rate"]) ** 2

candidates_a = []
candidates_b = []
for elec, us in unit_by_primary.items():
    if elec in TESTED:
        continue
    best_a = min(us, key=score_a)
    best_b = min(us, key=score_b)
    candidates_a.append({
        "electrode_id": elec,
        "distance_a": float(score_a(best_a) ** 0.5),
        "rate": best_a["baseline_rate_hz"],
        "corr": best_a["mean_correlation_top10"],
        "isi_cv": best_a["isi_cv"],
        "fp": best_a["footprint_extent"],
    })
    candidates_b.append({
        "electrode_id": elec,
        "distance_b": float(score_b(best_b) ** 0.5),
        "rate": best_b["baseline_rate_hz"],
        "corr": best_b["mean_correlation_top10"],
        "isi_cv": best_b["isi_cv"],
        "fp": best_b["footprint_extent"],
    })

candidates_a.sort(key=lambda x: x["distance_a"])
candidates_b.sort(key=lambda x: x["distance_b"])

print(f"\n=== Top 12 untested primaries by stable-core similarity (corr + isi_cv only) ===")
print(f"  {'elec':>5} {'dist':>6} {'rate':>6} {'corr':>6} {'isi_cv':>7} {'fp':>4}")
for c in candidates_a[:12]:
    print(f"  {c['electrode_id']:>5} {c['distance_a']:>6.2f} {c['rate']:>6.2f} {c['corr']:>6.2f} {c['isi_cv']:>7.2f} {c['fp']:>4}")

print(f"\n=== Top 12 by stable-core similarity (corr + isi_cv + rate) ===")
print(f"  {'elec':>5} {'dist':>6} {'rate':>6} {'corr':>6} {'isi_cv':>7} {'fp':>4}")
for c in candidates_b[:12]:
    print(f"  {c['electrode_id']:>5} {c['distance_b']:>6.2f} {c['rate']:>6.2f} {c['corr']:>6.2f} {c['isi_cv']:>7.2f} {c['fp']:>4}")

# Note: are the stable-core's PRIMARY electrodes themselves in TESTED?
print(f"\n=== Primary electrodes of stable-core units (untested are immediate cycle-9 candidates) ===")
for u in stable_features:
    elec = u["primary_electrode_id"]
    status = "TESTED" if elec in TESTED else "UNTESTED"
    print(f"  unit {u['unit_id']} -> primary elec {elec} [{status}]")

# Save everything
out = {
    "appearance_threshold": THR,
    "stable_core_uids": stable_core,
    "stable_core_centroid": CENTROID,
    "first_appearance_uids": FIRST_APPEARANCE,
    "overlap": sorted(list(overlap)),
    "stable_features": [{
        "unit_id": u["unit_id"],
        "primary_electrode_id": u["primary_electrode_id"],
        "baseline_rate_hz": u["baseline_rate_hz"],
        "mean_correlation_top10": u["mean_correlation_top10"],
        "isi_cv": u["isi_cv"],
        "footprint_extent": u["footprint_extent"],
        "n_appearances": appearance_count[u["unit_id"]],
    } for u in stable_features],
    "top_candidates_corr_isi": candidates_a[:20],
    "top_candidates_corr_isi_rate": candidates_b[:20],
    "primary_electrodes_of_stable_core": [
        {"unit_id": u["unit_id"], "primary_electrode": u["primary_electrode_id"], "tested": u["primary_electrode_id"] in TESTED}
        for u in stable_features
    ],
}
with open(PLAN / "scratch/stable_responder_analysis.json", "w") as f:
    json.dump(out, f, indent=2)
print(f"\nSaved {PLAN / 'scratch/stable_responder_analysis.json'}")
