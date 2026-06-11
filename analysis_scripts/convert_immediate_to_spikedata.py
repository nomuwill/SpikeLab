import pickle
import glob
import os
import numpy as np
from spikelab import SpikeData

BASE = '/home/sharf-lab/Desktop/Analysis_shared/data/pd_induction/immediate'


# ---------------------------------------------------------------------------
# Stub unpickler for IntegratedAnalysisTools pkl files
# ---------------------------------------------------------------------------
class _Stub:
    def __init__(self, *a, **kw): pass
    def __setstate__(self, d): self.__dict__.update(d)

class _StubUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        try:
            return super().find_class(module, name)
        except Exception:
            return type(name, (_Stub,), {})


# ---------------------------------------------------------------------------
# 260220_* subjects — already have spikedata.pkl; re-save as spikelab SpikeData
# ---------------------------------------------------------------------------
print('=== 260220_* (re-serialising existing pkls) ===\n')

for pkl_path in sorted(glob.glob(os.path.join(BASE, '260220_*', '*', 'spikedata.pkl'))):
    with open(pkl_path, 'rb') as f:
        old = _StubUnpickler(f).load()

    sd = SpikeData(
        old.train,
        length=old.length,
        neuron_attributes=old.neuron_attributes,
        metadata=old.metadata,
    )

    with open(pkl_path, 'wb') as f:
        pickle.dump(sd, f)

    rel = os.path.relpath(pkl_path, BASE)
    print(f'  {rel}  ({sd.N} units)')


# ---------------------------------------------------------------------------
# 251219_M07705 — one sorted.npz per well (no drug condition label)
# ---------------------------------------------------------------------------
print('\n=== 251219_M07705 ===\n')

def load_sorted_npz(path):
    data = np.load(path, allow_pickle=True)
    units = data['units']
    fs = float(data['fs'])
    locations = data['locations']

    train_ms = [u['spike_train'].astype(float) / fs * 1000.0 for u in units]
    neuron_attrs = [
        {
            'unit_id': int(u['unit_id']),
            'position': [float(u['x_max']), float(u['y_max'])],
            'electrode': int(u['electrode']),
            'template': np.asarray(u['template']),
        }
        for u in units
    ]
    metadata = {
        'fs': fs,
        'source_file': os.path.basename(path),
        'electrode_locations': locations,
    }
    return SpikeData(train_ms, neuron_attributes=neuron_attrs, metadata=metadata)

for npz_path in sorted(glob.glob(os.path.join(BASE, '251219_M07705', 'well*', 'sorted.npz'))):
    well_dir = os.path.dirname(npz_path)
    out_dir = os.path.join(well_dir, 'spikedata')
    os.makedirs(out_dir, exist_ok=True)
    sd = load_sorted_npz(npz_path)
    out_path = os.path.join(out_dir, 'sorted.pkl')
    with open(out_path, 'wb') as f:
        pickle.dump(sd, f)
    rel = os.path.relpath(out_path, BASE)
    print(f'  {rel}  ({sd.N} units)')


# ---------------------------------------------------------------------------
# 251222_M08754 — chunk0/sorted.npz + chunk1/sorted.npz per well
# ---------------------------------------------------------------------------
print('\n=== 251222_M08754 ===\n')

for well_dir in sorted(glob.glob(os.path.join(BASE, '251222_M08754', 'well*'))):
    out_dir = os.path.join(well_dir, 'spikedata')
    os.makedirs(out_dir, exist_ok=True)
    for chunk_dir in sorted(glob.glob(os.path.join(well_dir, 'chunk*'))):
        npz_path = os.path.join(chunk_dir, 'sorted.npz')
        chunk_name = os.path.basename(chunk_dir)
        sd = load_sorted_npz(npz_path)
        out_path = os.path.join(out_dir, chunk_name + '.pkl')
        with open(out_path, 'wb') as f:
            pickle.dump(sd, f)
        rel = os.path.relpath(out_path, BASE)
        print(f'  {rel}  ({sd.N} units)')

print('\nDone.')
