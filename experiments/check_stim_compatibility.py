"""Check stim-unit compatibility for a proposed list of stim electrodes.

Returns a JSON payload describing whether the list can all be wired together in
the same cfg, the per-electrode stim_unit assignments, and any conflicts (two
or more electrodes mapped to the same stim_unit).

Usage:
    /home/sharf-lab/MaxLab/python/bin/python3 check_stim_compatibility.py \
        --well 0 \
        --electrodes 5280,12464,8821,... \
        [--out path/to/output.json]

If --out is omitted, the JSON is printed to stdout (and nothing else is
written). On stderr, a short summary is always printed.

Output schema (see plan §7.3):
{
  "compatible": bool,
  "well": int,
  "n_proposed": int,
  "stim_unit_assignments": {electrode_id_str: stim_unit_id_int, ...},
  "conflicts": [{"electrodes": [...], "stim_unit": int}, ...]
}

Requires: maxlab (only available via /home/sharf-lab/MaxLab/python/bin/python3).
"""
import argparse
import json
import sys
import time
from pathlib import Path

import maxlab as mx
import maxlab.chip  # noqa: F401  -- imported for side-effect (registers Array)
import maxlab.util  # noqa: F401

THRESHOLD = 5.0


def parse_electrode_list(s: str) -> list[int]:
    if Path(s).exists():
        text = Path(s).read_text().strip()
        if text.startswith("["):
            return [int(e) for e in json.loads(text)]
        return [int(tok) for tok in text.replace(",", " ").split() if tok]
    return [int(tok) for tok in s.replace(",", " ").split() if tok]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--well", type=int, required=True)
    ap.add_argument("--electrodes", required=True,
                    help="Comma- or whitespace-separated electrode IDs, OR a "
                         "path to a file containing them (JSON list or "
                         "whitespace-separated).")
    ap.add_argument("--out", default=None,
                    help="Optional output JSON path. If omitted, prints JSON "
                         "to stdout.")
    args = ap.parse_args()

    candidates = parse_electrode_list(args.electrodes)
    if not candidates:
        print("error: empty electrode list", file=sys.stderr)
        return 2

    print(f"check_stim_compatibility: well={args.well} n_proposed={len(candidates)}",
          file=sys.stderr)

    mx.activate([args.well])
    mx.initialize([args.well])
    time.sleep(mx.Timing.waitInit)
    mx.send(mx.Core().enable_stimulation_power(True))
    mx.send_raw(f"stream_set_event_threshold {THRESHOLD}")

    arr = mx.Array("online")
    arr.reset()
    arr.select_electrodes(list(candidates))
    arr.select_stimulation_electrodes(list(candidates))
    arr.route()

    assignments: dict[str, int] = {}
    for elec in candidates:
        arr.connect_electrode_to_stimulation(elec)
        raw = arr.query_stimulation_at_electrode(elec)
        try:
            unit_id = int(str(raw).strip())
        except (TypeError, ValueError):
            unit_id = 0
        assignments[str(int(elec))] = int(unit_id)

    groups: dict[int, list[int]] = {}
    for elec_str, unit in assignments.items():
        groups.setdefault(unit, []).append(int(elec_str))
    conflict_list = [
        {"electrodes": sorted(elecs), "stim_unit": unit}
        for unit, elecs in groups.items()
        if len(elecs) > 1
    ]

    payload = {
        "compatible": len(conflict_list) == 0,
        "well": args.well,
        "n_proposed": len(candidates),
        "stim_unit_assignments": assignments,
        "conflicts": conflict_list,
    }

    text = json.dumps(payload, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)

    print(f"compatible={payload['compatible']} n_conflicts={len(conflict_list)}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
