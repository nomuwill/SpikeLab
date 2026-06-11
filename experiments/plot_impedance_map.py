#!/usr/bin/env python3
"""Plot electrode coupling map, optional hub cluster overlay.

Two modes:
  1. After 00_impedance_scan.py only: shows full array RMS + footprint +
     proposed 2-site stim locations (reads stim_sites.json).
  2. After select_hub_electrodes.py: shows full array RMS + both hub clusters
     (reads hub_config.json from --output-dir or --impedance-dir).

Reads impedance_results.npz from --impedance-dir.
Config files (hub_config.json or stim_sites.json) looked up first in
--output-dir, then --impedance-dir (same dir if only one is supplied).

Run with the automation conda env (MaxLab Python lacks matplotlib):
    conda run -n automation python \\
      /home/sharf-lab/Desktop/Research_automation/ephys_experiment_scripts/plot_impedance_map.py \\
      --impedance-dir PATH [--output-dir PATH] [--smooth-sigma 1.5]
"""

import argparse
import json
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
from scipy.ndimage import gaussian_filter


RESULTS_FILE   = "impedance_results.npz"
STIM_SITES_FILE = "stim_sites.json"
HUB_CONFIG_FILE = "hub_config.json"
PLOT_FILE      = "impedance_map.png"
HUB_PLOT_FILE  = "hub_overlay.png"

HUB_COLOURS = {"hub_a": "#00e5ff", "hub_b": "#ff6d00"}   # cyan / orange
HUB_LABELS  = {"hub_a": "Hub A", "hub_b": "Hub B"}


def load_results(impedance_dir):
    path = os.path.join(impedance_dir, RESULTS_FILE)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found — run 00_impedance_scan.py first"
        )
    return np.load(path)


def _find_json(filename, *dirs):
    """Return the first directory in dirs that contains filename, or None."""
    for d in dirs:
        if d and os.path.exists(os.path.join(d, filename)):
            return json.load(open(os.path.join(d, filename)))
    return None


def make_grid(data, num_rows, num_cols):
    """Sparse electrode data → dense 2D grid (NaN where unmeasured)."""
    grid = np.full((num_rows, num_cols), np.nan, dtype=np.float32)
    for el, rms, r, c in zip(
        data["electrodes"], data["rms_uv"], data["rows"], data["cols"]
    ):
        grid[int(r), int(c)] = float(rms)
    return grid


def smooth_grid(grid, sigma):
    fill_val = float(np.nanmedian(grid))
    filled = np.where(np.isnan(grid), fill_val, grid)
    smoothed = gaussian_filter(filled.astype(np.float64), sigma=sigma)
    smoothed[np.isnan(grid)] = np.nan
    return smoothed.astype(np.float32)


def _base_panel(fig, ax, grid, num_cols, num_rows, vmin, vmax, cmap, title, cbar_label):
    im = ax.imshow(
        grid, origin="upper", cmap=cmap,
        vmin=vmin, vmax=vmax, aspect="auto",
        extent=[0, num_cols, num_rows, 0],
    )
    cb = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cb.set_label(cbar_label, fontsize=9)
    ax.set_title(title, fontsize=10)
    ax.set_xlabel("Column (→ 220)", fontsize=8)
    ax.set_ylabel("Row (↓ 120)", fontsize=8)
    ax.tick_params(labelsize=7)


def overlay_hub_config(ax, hub_cfg):
    """Draw hub A and hub B electrode clusters on ax."""
    legend_handles = []
    for hub_key in ("hub_a", "hub_b"):
        hub  = hub_cfg.get(hub_key, {})
        col  = HUB_COLOURS[hub_key]
        lbl  = HUB_LABELS[hub_key]
        ctr  = hub.get("centre", {})

        for elec_info in hub.get("electrodes", []):
            r, c = elec_info["row"], elec_info["col"]
            ax.plot(c, r, "s", color=col, markersize=6,
                    markeredgecolor="white", markeredgewidth=0.6, zorder=10)

        # Centre marker (larger diamond)
        if ctr:
            ax.plot(ctr["col"], ctr["row"], "D", color=col, markersize=11,
                    markeredgecolor="black", markeredgewidth=1.5, zorder=12)
            ax.annotate(lbl, xy=(ctr["col"], ctr["row"]),
                        xytext=(ctr["col"] + 2, ctr["row"] - 3),
                        fontsize=7, color=col, fontweight="bold", zorder=13)

        legend_handles.append(
            Line2D([0], [0], marker="s", color="w", markerfacecolor=col,
                   markersize=8, label=f"{lbl} ({hub.get('n', '?')} electrodes)")
        )

    ax.legend(handles=legend_handles, loc="lower right", fontsize=7)


def overlay_stim_sites(ax, stim_info):
    """Draw old-style 2-site stim proposal (from 00_impedance_scan.py)."""
    confirmed  = stim_info.get("confirmed", False)
    colour     = "cyan" if confirmed else "yellow"
    status_tag = " [CONFIRMED]" if confirmed else " [unconfirmed]"
    for site in stim_info.get("sites", []):
        ax.plot(site["col"], site["row"], "D", color=colour, markersize=11,
                markeredgecolor="black", markeredgewidth=1.2, zorder=10)
        ax.annotate(f"Site {site['id']}\nel {site['electrode']}",
                    xy=(site["col"], site["row"]),
                    xytext=(site["col"] + 3, site["row"] - 3),
                    fontsize=6, color=colour, zorder=11)
    patch = mpatches.Patch(color=colour, label=f"Proposed stim sites{status_tag}")
    ax.legend(handles=[patch], loc="lower right", fontsize=7)


def main():
    parser = argparse.ArgumentParser(
        description="Plot electrode coupling map (+ optional hub cluster overlay)"
    )
    parser.add_argument("--impedance-dir", required=True,
                        help="Directory containing impedance_results.npz")
    parser.add_argument("--output-dir", default=None,
                        help="Directory to search for hub_config.json / stim_sites.json "
                             "and to write plots into.  Defaults to --impedance-dir.")
    parser.add_argument("--smooth-sigma", type=float, default=1.5,
                        help="Gaussian smoothing sigma in grid units (default: 1.5)")
    args = parser.parse_args()

    impedance_dir = os.path.abspath(args.impedance_dir)
    output_dir    = os.path.abspath(args.output_dir) if args.output_dir else impedance_dir

    data     = load_results(impedance_dir)
    hub_cfg  = _find_json(HUB_CONFIG_FILE,  output_dir, impedance_dir)
    stim_info = _find_json(STIM_SITES_FILE, output_dir, impedance_dir)

    num_rows = int(data.get("num_rows", 120))
    num_cols = int(data.get("num_cols", 220))

    grid_raw = make_grid(data, num_rows, num_cols)
    grid     = smooth_grid(grid_raw, args.smooth_sigma)
    vmin, vmax = float(np.nanpercentile(grid, 2)), float(np.nanpercentile(grid, 98))

    # ── Determine what to plot ────────────────────────────────────────────
    has_hubs = hub_cfg is not None

    # ── Figure 1: impedance map (always) ─────────────────────────────────
    fig1, axes = plt.subplots(1, 2, figsize=(18, 7))
    fig1.suptitle(
        "Electrode Coupling Map — Organoid Footprint\n"
        "(bright = higher RMS noise = better tissue contact)",
        fontsize=12, fontweight="bold",
    )

    _base_panel(fig1, axes[0], grid, num_cols, num_rows, vmin, vmax,
                "inferno", "Full Array — RMS Noise (µV)", "RMS noise (µV)")
    if has_hubs:
        overlay_hub_config(axes[0], hub_cfg)
    elif stim_info:
        overlay_stim_sites(axes[0], stim_info)

    # Right panel: footprint
    threshold = (
        stim_info.get("footprint_rms_threshold_uv") if stim_info
        else float(np.nanpercentile(grid, 70))
    )
    pct  = stim_info.get("footprint_percentile", 70) if stim_info else 70
    n_fp = stim_info.get("footprint_n_electrodes", "?") if stim_info else "?"
    footprint_grid = np.where(grid >= threshold, grid, np.nan)
    _base_panel(fig1, axes[1], footprint_grid, num_cols, num_rows, vmin, vmax,
                "viridis",
                f"Organoid Footprint — top {100 - pct:.0f}% RMS ({n_fp} electrodes)",
                "RMS noise (µV)")
    if has_hubs:
        overlay_hub_config(axes[1], hub_cfg)

    plt.tight_layout(rect=[0, 0, 1, 0.93])
    map_path = os.path.join(output_dir, PLOT_FILE)
    fig1.savefig(map_path, dpi=150, bbox_inches="tight")
    plt.close(fig1)
    print(f"Saved: {map_path}")

    # ── Figure 2: hub overlay close-up (only when hub_config.json exists) ─
    if has_hubs:
        fig2, ax2 = plt.subplots(figsize=(10, 6))
        fig2.suptitle("Hub Electrode Clusters (verify before starting daily sessions)",
                      fontsize=12, fontweight="bold")
        _base_panel(fig2, ax2, grid, num_cols, num_rows, vmin, vmax,
                    "inferno", "RMS Noise (µV) + Hub Clusters", "RMS noise (µV)")
        overlay_hub_config(ax2, hub_cfg)

        # Zoom to hub bounding box + margin
        all_rows = []
        all_cols = []
        for hub_key in ("hub_a", "hub_b"):
            for e in hub_cfg.get(hub_key, {}).get("electrodes", []):
                all_rows.append(e["row"])
                all_cols.append(e["col"])
        if all_rows:
            margin = 20
            ax2.set_xlim(max(0, min(all_cols) - margin),
                         min(num_cols, max(all_cols) + margin))
            ax2.set_ylim(min(num_rows, max(all_rows) + margin),
                         max(0, min(all_rows) - margin))

        overlay_path = os.path.join(output_dir, HUB_PLOT_FILE)
        fig2.savefig(overlay_path, dpi=150, bbox_inches="tight")
        plt.close(fig2)
        print(f"Saved: {overlay_path}")

        print(f"\nHub summary:")
        for hub_key in ("hub_a", "hub_b"):
            hub = hub_cfg.get(hub_key, {})
            elecs = [e["electrode"] for e in hub.get("electrodes", [])]
            print(f"  {HUB_LABELS[hub_key]}: {hub.get('n', len(elecs))} electrodes — {elecs}")
        print(f"\nIf hub placement looks good, start daily sessions.")
        print(f"If not, re-run select_hub_electrodes.py with a different --seed.")


if __name__ == "__main__":
    main()
