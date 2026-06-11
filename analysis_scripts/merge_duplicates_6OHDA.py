import pickle
import glob
import os
import numpy as np
from spikelab import SpikeData

BASE = '/home/sharf-lab/Desktop/Analysis_shared/data/6OHDA'


def add_waveform_attrs(sd):
    """Add avg_waveform and traces_meta to neuron_attributes from template/channel fields."""
    if sd.neuron_attributes is None:
        raise ValueError("neuron_attributes is None — cannot run merge without waveform data.")
    for attrs in sd.neuron_attributes:
        template = attrs.get('template')
        channel = attrs.get('channel')
        if template is not None and 'avg_waveform' not in attrs:
            attrs['avg_waveform'] = np.asarray(template, dtype=float).reshape(1, -1)
        if channel is not None and 'traces_meta' not in attrs:
            attrs['traces_meta'] = {'channels': [int(channel)]}


pkl_files = sorted(glob.glob(os.path.join(BASE, '**', 'spikedata', '*.pkl'), recursive=True))
# Skip any previously created _merged files
pkl_files = [p for p in pkl_files if not p.endswith('_merged.pkl')]

print(f'Found {len(pkl_files)} recordings to process.\n')

total_before = 0
total_after = 0

for pkl_path in pkl_files:
    with open(pkl_path, 'rb') as f:
        sd = pickle.load(f)

    add_waveform_attrs(sd)

    sd_merged, result = sd.curate_by_merge_duplicates()

    n_before = sd.N
    n_after = sd_merged.N
    n_merged = n_before - n_after
    total_before += n_before
    total_after += n_after

    stem = os.path.splitext(pkl_path)[0]
    out_path = stem + '_merged.pkl'
    with open(out_path, 'wb') as f:
        pickle.dump(sd_merged, f)

    rel = os.path.relpath(out_path, BASE)
    print(f'  {rel}')
    print(f'    {n_before} → {n_after} units  ({n_merged} merged)')

print(f'\nDone. Total: {total_before} → {total_after} units ({total_before - total_after} merged across all recordings).')
