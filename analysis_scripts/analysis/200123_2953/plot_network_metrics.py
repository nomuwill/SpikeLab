"""Compute and plot global efficiency and node strength for D0, D10, D50."""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import networkx as nx
import os

from spikelab.workspace.workspace import AnalysisWorkspace

SCRIPT_DIR = os.path.dirname(__file__)
WS_PATH = os.path.join(SCRIPT_DIR, "results", "workspace")
FIG_DIR = os.path.join(SCRIPT_DIR, "figures")
os.makedirs(FIG_DIR, exist_ok=True)

ws = AnalysisWorkspace.load(WS_PATH)

conditions = ["D0", "D10", "D50"]
labels = {"D0": "0 µM (control)", "D10": "10 µM", "D50": "50 µM"}

# --- Compute global efficiency and node strength ---
efficiencies = {}
node_strengths = {}

for cond in conditions:
    sttc = ws.get(cond, f"sttc_{cond}")
    G = sttc.to_networkx(threshold=0.1, invert_weights=True)
    efficiencies[cond] = nx.global_efficiency(G)
    strengths = np.array([d for _, d in G.degree(weight="weight")])
    node_strengths[cond] = strengths
    print(f"{cond}: global efficiency = {efficiencies[cond]:.4f}, "
          f"mean node strength = {strengths.mean():.2f} ± {strengths.std():.2f}")

# --- Figure: global efficiency bar chart + node strength distributions ---
fig, axes = plt.subplots(1, 2, figsize=(10, 4))

# Panel 1: Global efficiency
ax = axes[0]
conds_list = list(conditions)
eff_vals = [efficiencies[c] for c in conds_list]
colors = ["#2166ac", "#f4a582", "#b2182b"]
ax.bar(range(len(conds_list)), eff_vals, color=colors, edgecolor="black", linewidth=0.5)
ax.set_xticks(range(len(conds_list)))
ax.set_xticklabels([labels[c] for c in conds_list], fontsize=10)
ax.set_ylabel("Global efficiency", fontsize=11)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.tick_params(direction="out")

# Panel 2: Node strength distributions
ax = axes[1]
parts = ax.violinplot(
    [node_strengths[c] for c in conds_list],
    positions=range(len(conds_list)),
    showmeans=True,
    showextrema=False,
)
for pc, color in zip(parts["bodies"], colors):
    pc.set_facecolor(color)
    pc.set_alpha(0.7)
parts["cmeans"].set_color("black")
ax.set_xticks(range(len(conds_list)))
ax.set_xticklabels([labels[c] for c in conds_list], fontsize=10)
ax.set_ylabel("Node strength (sum of weights)", fontsize=11)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.tick_params(direction="out")

fig.tight_layout()
fig.savefig(
    os.path.join(FIG_DIR, "network_metrics_d0_d10_d50.png"),
    dpi=150, bbox_inches="tight",
)
plt.close(fig)
print("Saved figures/network_metrics_d0_d10_d50.png")
