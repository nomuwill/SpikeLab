"""Compute and plot STTC matrices for D0, D10, and D50."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import os

from spikelab.workspace.workspace import AnalysisWorkspace

SCRIPT_DIR = os.path.dirname(__file__)
WS_PATH = os.path.join(SCRIPT_DIR, "results", "workspace")
FIG_DIR = os.path.join(SCRIPT_DIR, "figures")
os.makedirs(FIG_DIR, exist_ok=True)

ws = AnalysisWorkspace.load(WS_PATH)

conditions = ["D0", "D10", "D50"]
labels = {"D0": "0 µM (control)", "D10": "10 µM", "D50": "50 µM"}

sttc_matrices = {}
for cond in conditions:
    sd = ws.get(cond, "spikedata")
    key = f"sttc_{cond}"
    if ws.get(cond, key) is not None:
        sttc = ws.get(cond, key)
    else:
        print(f"Computing STTC for {cond}...")
        sttc = sd.spike_time_tilings(delt=20.0)
        ws.store(cond, key, sttc)
    sttc_matrices[cond] = sttc

ws.save(WS_PATH)
print("STTC matrices cached in workspace.")

# Plot side by side
fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))

for ax, cond in zip(axes, conditions):
    sttc = sttc_matrices[cond]
    sttc.plot(
        ax=ax,
        cmap="RdBu_r",
        vmin=-0.3,
        vmax=0.3,
        colorbar_label="STTC" if cond == "D50" else "",
    )
    ax.set_xlabel("Unit", fontsize=11)
    ax.set_ylabel("Unit", fontsize=11)
    ax.set_xticklabels([])
    ax.set_yticklabels([])
    ax.text(
        0.5, 1.05, labels[cond],
        transform=ax.transAxes, ha="center", va="bottom", fontsize=12,
    )

fig.savefig(
    os.path.join(FIG_DIR, "sttc_d0_d10_d50.png"), dpi=150, bbox_inches="tight"
)
plt.close(fig)
print("Saved figures/sttc_d0_d10_d50.png")
