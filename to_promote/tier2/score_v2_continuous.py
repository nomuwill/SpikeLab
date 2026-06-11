"""
Compute mean-AUROC-over-v2-top-15-core (the new primary metric per rec #20)
for ALL conditions across ALL cycles. This is the score going forward.
"""

from __future__ import annotations
import json, pickle
from collections import defaultdict
from pathlib import Path

import numpy as np

PLAN = Path("/home/sharf-lab/Desktop/Research_automation/orchestrator/stim-optimize-maxone_2026-04-20")
WELL = PLAN / "well_0"
PRE_LEN, EVK_LEN = 50.0, 195.0
V2_TOP_15 = [419, 436, 434, 411, 556, 435, 601, 415, 416, 466, 391, 572, 633, 424, 400]

def auroc(evk_rates, pre_rates):
    n_e, n_p = len(evk_rates), len(pre_rates)
    if n_e == 0 or n_p == 0: return float("nan")
    combined = np.concatenate([evk_rates, pre_rates])
    if combined.std() == 0: return 0.5
    ranks = combined.argsort().argsort() + 1
    U = ranks[:n_e].sum() - n_e * (n_e + 1) / 2
    return U / (n_e * n_p)

def compute_for_cycle(K, target_label=None):
    """Returns dict {condition_label: {top15_aurocs, mean_AUROC_top15, n_AUROC>0.6, max_AUROC}}."""
    cache_path = WELL / f"cycle_{K}/sort_cache.pkl"
    if not cache_path.exists():
        return None
    with open(cache_path, "rb") as f:
        cache = pickle.load(f)
    slices = cache["slices_list"]
    if not slices:
        return None
    n_events = len(slices)
    n_units = getattr(slices[0], "N", len(slices[0].train))

    # Find the manifest
    rec_dirs = list(PLAN.glob(f"recordings/*/*cycle{K}*")) + list(PLAN.glob(f"recordings/*/*cycle_{K}*"))
    rec_dir = None
    for d in rec_dirs:
        if (d / "manifest.json").exists():
            man = json.load(open(d / "manifest.json"))
            rn = man.get("steps", {}).get("record", [{}])[0].get("recording_name", "")
            if rn == f"cycle_{K}" or "_old" not in str(d):
                rec_dir = d
                break
    if rec_dir is None:
        return None
    manifest = json.load(open(rec_dir / "manifest.json"))
    events = manifest["steps"]["record"][0]["events"]
    cond_to_event_idxs = defaultdict(list)
    for ei, e in enumerate(events):
        L = e["label"]
        if "_t" in L:
            L = L.rsplit("_t", 1)[0]
        cond_to_event_idxs[L].append(ei)

    # Pre-extract counts
    pre_mat = np.zeros((n_events, n_units), dtype=np.int32)
    evk_mat = np.zeros((n_events, n_units), dtype=np.int32)
    for ei, slc in enumerate(slices):
        if not hasattr(slc, "train"):
            continue
        for u in range(min(n_units, getattr(slc, "N", len(slc.train)))):
            sp = np.asarray(slc.train[u])
            if len(sp) == 0:
                continue
            pre_mat[ei, u] = int(np.sum((sp >= -50) & (sp < 0)))
            evk_mat[ei, u] = int(np.sum((sp >= 5) & (sp < 200)))

    out = {}
    for label, evidx in cond_to_event_idxs.items():
        if target_label is not None and label != target_label:
            continue
        sel = np.array(evidx)
        pre_rates = pre_mat[sel] / PRE_LEN
        evk_rates = evk_mat[sel] / EVK_LEN
        aurocs = np.array([auroc(evk_rates[:, u], pre_rates[:, u]) for u in range(n_units)])
        top15_aurocs = np.array([aurocs[u] for u in V2_TOP_15 if u < len(aurocs)])
        mean_top15 = float(np.nanmean(top15_aurocs))
        sd_top15 = float(np.nanstd(top15_aurocs))
        n_above = int(np.sum(aurocs > 0.6))
        max_a = float(np.nanmax(aurocs))
        out[label] = {
            "mean_AUROC_top15_core_PRIMARY_METRIC": mean_top15,
            "sd_AUROC_top15_core": sd_top15,
            "n_AUROC>0.6_secondary": n_above,
            "max_AUROC": max_a,
            "per_top15_unit_AUROC": {str(u): float(aurocs[u]) if u < len(aurocs) else None for u in V2_TOP_15},
        }
    return out

if __name__ == "__main__":
    import sys
    cycles = [int(x) for x in sys.argv[1:]] if len(sys.argv) > 1 else range(1, 15)
    print(f"=== Continuous v2 score (mean AUROC over v2 top-15 core) — primary metric per rec #20 ===")
    print(f"  V2_TOP_15 = {V2_TOP_15}")
    print()
    all_results = {}
    for K in cycles:
        r = compute_for_cycle(K)
        if r is None: continue
        all_results[K] = r
        print(f"Cycle {K}:")
        sorted_conds = sorted(r.items(), key=lambda x: -x[1]["mean_AUROC_top15_core_PRIMARY_METRIC"])
        for label, d in sorted_conds:
            print(f"  {label:<55} score_v2={d['mean_AUROC_top15_core_PRIMARY_METRIC']:.4f} (sd={d['sd_AUROC_top15_core']:.3f}, n>0.6={d['n_AUROC>0.6_secondary']:>2})")
        print()
    with open(PLAN / "scratch/score_v2_continuous_per_cycle.json", "w") as f:
        json.dump({str(K): v for K, v in all_results.items()}, f, indent=2)
    print(f"Saved {PLAN / 'scratch/score_v2_continuous_per_cycle.json'}")
