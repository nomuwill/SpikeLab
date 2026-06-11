"""Load all diazepam conditions into an AnalysisWorkspace."""

import os
import sys
import pickle

import spikelab
# Alias old module path so pickle can find the class
sys.modules["SpikeLab"] = spikelab
sys.modules["SpikeLab.spikedata"] = spikelab.spikedata
sys.modules["SpikeLab.spikedata.spikedata"] = spikelab.spikedata.spikedata

from spikelab.spikedata.spikedata import SpikeData
from spikelab.workspace.workspace import AnalysisWorkspace

DATA_DIR = "/home/sharf-lab/Desktop/Analysis_shared/data/200123_2953"
CONDITIONS = ["D0", "D3", "D10", "D30", "D50"]
WS_PATH = os.path.join(
    os.path.dirname(__file__), "results", "workspace"
)

os.makedirs(os.path.dirname(WS_PATH), exist_ok=True)

ws = AnalysisWorkspace(name="200123_2953_diazepam")

for cond in CONDITIONS:
    pkl_path = os.path.join(DATA_DIR, cond, "spikedata.pkl")
    with open(pkl_path, "rb") as f:
        sd = pickle.load(f)
    assert isinstance(sd, SpikeData), f"{cond}: expected SpikeData, got {type(sd)}"

    # Patch missing attributes from older pickle format
    if not hasattr(sd, "start_time"):
        sd.start_time = 0.0

    ws.store(cond, "spikedata", sd)
    conc = cond[1:]  # strip 'D' prefix
    print(f"\n--- {cond} ({conc} µM diazepam) ---")
    print(f"  Units:    {sd.N}")
    print(f"  Duration: {sd.length / 1000:.1f} s ({sd.length / 60000:.1f} min)")
    fr = [len(t) / (sd.length / 1000) for t in sd.train]
    print(f"  Mean FR:  {sum(fr) / len(fr):.2f} Hz")
    meta_keys = list(sd.metadata.keys()) if sd.metadata else []
    print(f"  Metadata keys: {meta_keys}")

ws.save(WS_PATH)
print(f"\nWorkspace saved to {WS_PATH}")
ws.describe()
