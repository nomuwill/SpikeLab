"""Plot raster + population rate for first 60s of D0."""

import matplotlib
matplotlib.use("Agg")
import os

from spikelab.workspace.workspace import AnalysisWorkspace
from spikelab.spikedata.plot_utils import plot_recording

ws = AnalysisWorkspace.load(
    os.path.join(os.path.dirname(__file__), "results", "workspace")
)
sd = ws.get("D0", "spikedata")
sd_60s = sd.subtime(0, 60000)

fig = plot_recording(sd_60s, show_raster=True, show_pop_rate=True)

fig_dir = os.path.join(os.path.dirname(__file__), "figures")
os.makedirs(fig_dir, exist_ok=True)
fig.savefig(os.path.join(fig_dir, "d0_raster_60s.png"), dpi=150, bbox_inches="tight")
print("Saved figures/d0_raster_60s.png")
