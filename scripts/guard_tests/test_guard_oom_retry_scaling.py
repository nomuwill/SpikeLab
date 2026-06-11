"""Stress test #5: KS2 OOM auto-retry scaling.

The retry loop in ``sort_recording`` halves Kilosort2's ``NT``
(per-batch sample count) on each ``GPUOutOfMemoryError`` and retries
up to ``oom_max`` times. Each halving roughly halves per-batch VRAM
— so the backend recovers automatically when the user's NT is too
big for available memory.

Tests the backend's contract directly:

* ``scale_oom_params(0.5)`` halves NT, rounds down to a multiple of
  32 (KS2 kernel constraint), and returns True.
* Repeated halving terminates at NT < 1024 with a False return —
  the documented "further reduction is unlikely to help" floor.
* Invalid factors (≤ 0, ≥ 1) return False without mutating params.
* ``snapshot_oom_params`` / ``restore_oom_params`` round-trip the
  scaled value back to its original.

This is the only auto-recovering guard in the pipeline — every
other guard *aborts* the sort with a classified error, but OOM
recovery is expected to keep the sort running.
"""
import sys

from spikelab.spike_sorting.backends.kilosort2 import Kilosort2Backend
from spikelab.spike_sorting.config import SortingPipelineConfig


def main() -> int:
    cfg = SortingPipelineConfig.from_kwargs(
        stream_id="well000",
        use_docker=True,
    )

    backend = Kilosort2Backend(cfg)

    # Resolve the initial NT (None → 64*1024 + ntbuff at first scale).
    initial_params = dict(backend.config.sorter.sorter_params or {})
    print(f"Initial sorter_params: NT={initial_params.get('NT')!r}, "
          f"ntbuff={initial_params.get('ntbuff')!r}")

    # 1. Snapshot for later restore.
    snap = backend.snapshot_oom_params()
    print(f"Snapshot: {snap}\n")

    # 2. Halve NT repeatedly until the backend refuses.
    print("=" * 60)
    print("Halving NT until backend refuses (NT < 1024)")
    print("=" * 60)
    halvings: list[int] = []
    while True:
        scaled = backend.scale_oom_params(0.5)
        nt_after = backend.config.sorter.sorter_params.get("NT")
        if not scaled:
            print(f"  refused at NT={nt_after}\n")
            break
        halvings.append(int(nt_after))
        if len(halvings) > 50:
            print("FAIL: scaling never refused after 50 halvings — infinite loop?")
            return 1

    if not halvings:
        print("FAIL: first halving was refused — backend never scaled at all")
        return 1
    final_nt = halvings[-1]
    if final_nt < 1024:
        print(f"FAIL: last accepted NT was {final_nt}, below the 1024 floor")
        return 1
    if any(nt % 32 != 0 for nt in halvings):
        print(f"FAIL: a halving produced an NT not divisible by 32: "
              f"{[nt for nt in halvings if nt % 32 != 0]}")
        return 1
    print(f"PASS: NT halved {len(halvings)} times: {halvings}")
    print(f"      final accepted NT = {final_nt} (≥ 1024, divisible by 32)")

    # 3. Invalid factors return False without mutating.
    print("\n" + "=" * 60)
    print("Invalid factors should return False without mutating NT")
    print("=" * 60)
    nt_before = backend.config.sorter.sorter_params.get("NT")
    for bad_factor in [0.0, -0.5, 1.0, 1.5, 2.0]:
        scaled = backend.scale_oom_params(bad_factor)
        nt_after = backend.config.sorter.sorter_params.get("NT")
        if scaled:
            print(f"FAIL: factor={bad_factor} returned True (should be False)")
            return 1
        if nt_after != nt_before:
            print(f"FAIL: factor={bad_factor} mutated NT "
                  f"({nt_before} -> {nt_after})")
            return 1
        print(f"  factor={bad_factor!s:5}: refused, NT unchanged at {nt_after}")
    print("PASS: all invalid factors refused without mutation")

    # 4. Snapshot/restore round-trip.
    print("\n" + "=" * 60)
    print("Snapshot/restore round-trip")
    print("=" * 60)
    pre_restore = backend.config.sorter.sorter_params
    backend.restore_oom_params(snap)
    post_restore = backend.config.sorter.sorter_params
    print(f"sorter_params before restore: {pre_restore}")
    print(f"sorter_params after  restore: {post_restore}")
    if post_restore != snap["sorter_params"]:
        print(f"FAIL: restore did not bring sorter_params back to "
              f"{snap['sorter_params']!r}")
        return 1
    print("PASS: restore brought sorter_params back to the snapshot")

    # 5. After restore, scaling should work again from the original.
    scaled = backend.scale_oom_params(0.5)
    nt_after_first_halve = (backend.config.sorter.sorter_params or {}).get("NT")
    print(f"\nAfter restore + one halve: scaled={scaled}, NT={nt_after_first_halve}")
    if not scaled:
        print("FAIL: first halve after restore returned False")
        return 1
    if nt_after_first_halve is None:
        print("FAIL: halve did not populate NT in sorter_params")
        return 1
    # The first halve should resolve NT=None → 65600, then scale to 32800.
    if nt_after_first_halve != 32800:
        print(f"FAIL: expected NT=32800 after first halve from default, "
              f"got {nt_after_first_halve}")
        return 1
    print("PASS: halving works after restore (resolves NT default → 32800)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
