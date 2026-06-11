"""
Compute proper §7.5 drift metrics per cycle using cached sort outputs:
  - log2_total_rate_ratio (cycle_K / cycle_1)
  - fraction_units_>2x_change (proxy for fraction_electrodes_>2x_change)
  - spatial_correlation (per-unit rate vector cycle K vs cycle 1)
  - drift_alert_level: null | "minor" | "major"

Also compute score-stability metrics (NOT in the plan, additive):
  - For each condition tested in >= 2 cycles, compute CV of scores
  - Per cycle, summarize how many of its conditions had been previously seen
    and how reproducible those were.

Updates trajectory.json in place with corrected drift_alert_level + a new
score_stability_alert field per cycle.
"""

from __future__ import annotations
import json
import math
import pickle
from pathlib import Path

import numpy as np

PLAN = Path("/home/sharf-lab/Desktop/Research_automation/orchestrator/stim-optimize-maxone_2026-04-20")
WELL = PLAN / "well_0"

# Per-cycle baseline rate vectors --------------------------------------------
def per_unit_rates(cycle):
    cache = WELL / f"cycle_{cycle}/sort_cache.pkl"
    if not cache.exists():
        return None
    with open(cache, "rb") as f:
        d = pickle.load(f)
    bt = d["baseline_trains"]
    fs = d.get("fs_hz", 20000.0)
    rates = {}
    BASELINE_S = 180.0
    for uid, sp_ms in bt.items():
        n = int(np.sum((sp_ms >= 0) & (sp_ms < BASELINE_S * 1000.0)))
        rates[int(uid)] = n / BASELINE_S
    return rates

cycle_rates = {}
for k in range(1, 8):
    r = per_unit_rates(k)
    if r is not None:
        cycle_rates[k] = r
        print(f"cycle {k}: {len(r)} units, total rate {sum(r.values()):.1f} Hz")

# Reference: cycle 1
ref = cycle_rates[1]
ref_uids = sorted(ref.keys())

# Drift metrics per cycle (vs cycle 1) ---------------------------------------
def drift_metrics(K):
    target = cycle_rates[K]
    common = [u for u in ref_uids if u in target]
    r1 = np.array([ref[u] for u in common])
    rk = np.array([target[u] for u in common])
    total1 = r1.sum()
    totalk = rk.sum()
    log2_ratio = math.log2(totalk / max(total1, 1e-9))
    # fraction with >2x change in either direction
    valid = r1 > 0.05  # avoid divide-by-tiny
    if valid.sum() > 0:
        log2_per = np.log2(np.maximum(rk[valid], 1e-9) / np.maximum(r1[valid], 1e-9))
        frac_2x = float((np.abs(log2_per) > 1.0).mean())
    else:
        frac_2x = float("nan")
    # spatial correlation
    if len(r1) > 1 and r1.std() > 0 and rk.std() > 0:
        corr = float(np.corrcoef(r1, rk)[0, 1])
    else:
        corr = 1.0
    return {
        "log2_total_rate_ratio": float(log2_ratio),
        "fraction_units_>2x_change": frac_2x,
        "spatial_correlation": corr,
        "n_units_compared": int(len(common)),
    }

def alert_level(m):
    if (abs(m["log2_total_rate_ratio"]) > 1.0
        or m["fraction_units_>2x_change"] > 0.5
        or m["spatial_correlation"] < 0.4):
        return "major"
    if (abs(m["log2_total_rate_ratio"]) > 0.5
        or m["fraction_units_>2x_change"] > 0.30
        or m["spatial_correlation"] < 0.7):
        return "minor"
    return None

print("\n=== §7.5 drift metrics per cycle (vs cycle 1) ===")
print(f"{'cycle':>6} {'log2_ratio':>11} {'frac_2x':>9} {'spatial_corr':>13} {'alert_level':>13}")
drift_per_cycle = {}
for K in sorted(cycle_rates.keys()):
    if K == 1:
        m = {"log2_total_rate_ratio": 0.0, "fraction_units_>2x_change": 0.0, "spatial_correlation": 1.0, "n_units_compared": len(ref)}
        alert = None
    else:
        m = drift_metrics(K)
        alert = alert_level(m)
    drift_per_cycle[K] = {**m, "drift_alert_level": alert}
    print(f"{K:>6} {m['log2_total_rate_ratio']:>11.3f} {m['fraction_units_>2x_change']:>9.3f} {m['spatial_correlation']:>13.3f} {str(alert):>13}")

# Score-stability metrics (NOT in plan, supplementary) ----------------------
# For each condition tested in >= 2 cycles, compute mean / SD / CV of scores.
condition_history = {}  # label -> [(cycle, score, n_resp)]
for K in range(1, 8):
    aj = WELL / f"cycle_{K}/analysis.json"
    if not aj.exists():
        continue
    a = json.load(open(aj))
    for c in a["conditions"]:
        condition_history.setdefault(c["label"], []).append((K, c["score"], c["n_responsive"]))

repeat_conditions = {k: v for k, v in condition_history.items() if len(v) >= 2}
print(f"\n=== Score stability (conditions tested in >=2 cycles) ===")
print(f"  {'condition':<55} {'n_meas':>7} {'mean':>6} {'sd':>6} {'cv':>6} {'range':>14}")
score_stability = {}
for label, hist in sorted(repeat_conditions.items(), key=lambda kv: -np.std([s for _, s, _ in kv[1]])):
    scores = np.array([s for _, s, _ in hist])
    mean = float(scores.mean())
    sd = float(scores.std(ddof=0))
    cv = sd / max(mean, 1e-3)
    score_stability[label] = {
        "n_measurements": len(scores),
        "scores_by_cycle": {str(c): float(s) for c, s, _ in hist},
        "mean": mean, "sd": sd, "cv": cv,
        "range_min": float(scores.min()), "range_max": float(scores.max()),
    }
    print(f"  {label:<55} {len(scores):>7} {mean:>6.2f} {sd:>6.2f} {cv:>6.2f} {scores.min():>5.2f}-{scores.max():>4.2f}")

# Define score_stability_alert per cycle
# A cycle's alert level = max alert level over its repeat-conditions:
#   - "high" if any repeat-condition has CV > 0.5 AND scores span >= 0.3
#   - "moderate" if CV > 0.3
#   - null otherwise
def cycle_stability_alert(K):
    repeats_in_K = []
    for label, hist in repeat_conditions.items():
        cycles_seen = [c for c, _, _ in hist]
        if K in cycles_seen and min(cycles_seen) < K:
            repeats_in_K.append(label)
    worst = None
    for label in repeats_in_K:
        s = score_stability[label]
        span = s["range_max"] - s["range_min"]
        if s["cv"] > 0.5 and span >= 0.3:
            return "high"
        if s["cv"] > 0.3:
            worst = "moderate"
    return worst

print("\n=== Score stability alert per cycle ===")
print(f"  {'cycle':>6} {'alert':>10} {'repeat_conditions_tested':>30}")
score_stability_alert = {}
for K in sorted(cycle_rates.keys()):
    if K == 1:
        score_stability_alert[K] = None
        n_repeats = 0
    else:
        score_stability_alert[K] = cycle_stability_alert(K)
        n_repeats = sum(1 for label, hist in repeat_conditions.items()
                       if K in [c for c, _, _ in hist] and min(c for c, _, _ in hist) < K)
    print(f"  {K:>6} {str(score_stability_alert[K]):>10} {n_repeats:>30}")

# Save metrics --------------------------------------------------------------
out = {
    "drift_per_cycle": drift_per_cycle,
    "score_stability_per_condition": score_stability,
    "score_stability_alert_per_cycle": score_stability_alert,
}
with open(PLAN / "scratch/drift_and_stability_metrics.json", "w") as f:
    json.dump(out, f, indent=2)
print(f"\nSaved {PLAN / 'scratch/drift_and_stability_metrics.json'}")

# Back-fill trajectory.json -------------------------------------------------
traj_path = WELL / "trajectory.json"
traj = json.load(open(traj_path))
for c in traj["cycles"]:
    K = c["cycle"]
    dm = drift_per_cycle.get(K)
    if dm:
        c["drift_alert_level"] = dm["drift_alert_level"]
        c["baseline_drift_metrics"] = {
            "log2_total_rate_ratio": dm["log2_total_rate_ratio"],
            "fraction_units_>2x_change": dm["fraction_units_>2x_change"],
            "spatial_correlation": dm["spatial_correlation"],
        }
    c["score_stability_alert"] = score_stability_alert.get(K)
with open(traj_path, "w") as f:
    json.dump(traj, f, indent=2)
print(f"\nUpdated {traj_path}")
