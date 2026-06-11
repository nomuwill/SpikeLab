import numpy as np
import zipfile
import io
import os
import glob
import re
import pickle
from spikelab import SpikeData

BASE = '/home/sharf-lab/Desktop/Analysis_shared/data/6OHDA'


def extract_timepoint(filepath):
    stem = os.path.splitext(os.path.basename(filepath))[0]
    stem = stem.replace('_acqm', '')
    stem = re.sub(r'_well\d+$', '', stem)

    if re.search(r'BASELINE', stem, re.IGNORECASE):
        return 'baseline'

    m = re.search(r'_(T[12]_.+)$', stem)
    if m:
        return m.group(1)

    # Standalone hour suffix: "Control-24hr"
    m = re.search(r'[-_](\d+hr)$', stem, re.IGNORECASE)
    if m:
        return m.group(1)

    # Pre-treatment file: ID_D{day}_{date}
    m = re.search(r'_D(\d+)_\d+$', stem)
    if m:
        return 'D' + m.group(1)

    # Day-only fallback: no explicit timepoint label (e.g. 23137 Control series)
    m = re.search(r'_D(\d+)_', stem)
    if m:
        return 'D' + m.group(1)

    return stem


def load_spikedata(path, from_zip=False):
    if from_zip:
        with zipfile.ZipFile(path) as z:
            with z.open('qm.npz') as f:
                data = np.load(io.BytesIO(f.read()), allow_pickle=True)
    else:
        data = np.load(path, allow_pickle=True)

    train_dict = data['train'].item()
    fs = float(data['fs'].item() if data['fs'].ndim == 0 else data['fs'][0])

    rp = data['redundant_pairs']
    if rp.ndim == 0:
        rp = rp.item()

    nd = data['neuron_data'].item()
    config = data['config'].item() if data['config'].ndim == 0 else data['config']

    unit_ids = sorted(train_dict.keys())
    train_ms = [train_dict[uid].astype(float) / fs * 1000.0 for uid in unit_ids]
    neuron_attrs = [nd.get(uid, {}) for uid in unit_ids]

    metadata = {
        'unit_ids': unit_ids,
        'fs': fs,
        'redundant_pairs': rp,
        'config': config,
        'source_file': os.path.basename(path),
    }

    return SpikeData(train_ms, neuron_attributes=neuron_attrs, metadata=metadata)


def process_dir(src_dir, files, from_zip=False):
    out_dir = os.path.join(src_dir, 'spikedata')
    os.makedirs(out_dir, exist_ok=True)
    for fp in sorted(files):
        tp = extract_timepoint(fp)
        out_path = os.path.join(out_dir, tp + '.pkl')
        sd = load_spikedata(fp, from_zip=from_zip)
        with open(out_path, 'wb') as f:
            pickle.dump(sd, f)
        print(f'  {tp}.pkl  ({sd.N} units)')


# NPZ subdirs
for subdir in ['24481', '24578']:
    src = os.path.join(BASE, subdir)
    files = glob.glob(os.path.join(src, '*.npz'))
    print(f'\n{subdir}/')
    process_dir(src, files, from_zip=False)

# acqm.zip subdirs (per-well)
for subdir in ['M06359', 'M06943', 'M08754']:
    for well_dir in sorted(glob.glob(os.path.join(BASE, subdir, 'well*'))):
        well = os.path.basename(well_dir)
        files = glob.glob(os.path.join(well_dir, '*.zip'))
        print(f'\n{subdir}/{well}/')
        process_dir(well_dir, files, from_zip=True)

print('\nDone.')
