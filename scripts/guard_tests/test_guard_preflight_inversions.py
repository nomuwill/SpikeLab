"""Stress test #4: preflight surfaces classified findings on bad inputs.

Three inversions exercising different finding levels:

1. ``preflight_min_free_inter_gb`` higher than total disk → WARN-level
   ``low_disk_inter`` finding. Disk-low is always WARN by design
   (the runtime ``DiskUsageWatchdog`` is the hard fail) — the
   preflight just gives the operator a heads-up.
2. ``hdf5_plugin_path`` pointing at a missing directory → FAIL-level
   ``hdf5_plugin_missing`` finding. This is the hard guard that
   prevents starting a sort that will definitely fail at load time.
3. Recording file path that doesn't exist → FAIL-level
   ``recording_missing`` finding.

Doesn't run a real sort; preflight is the cheap pre-loop pass and
deserves its own isolated test.
"""
import shutil
import sys
from pathlib import Path

from spikelab.spike_sorting.config import SortingPipelineConfig
from spikelab.spike_sorting.guards import (
    PreflightFinding,
    report_findings,
    run_preflight,
)

TMP = Path("/tmp/preflight_stress_test")
BAD_PLUGIN = Path("/tmp/this_plugin_dir_does_not_exist_xyz")


def _findings_with_code(findings, code: str) -> list[PreflightFinding]:
    return [f for f in findings if f.code == code]


def main() -> int:
    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True)
    inter = TMP / "inter"
    res = TMP / "res"
    inter.mkdir()
    res.mkdir()

    # Use the user's real recording so recording validation passes.
    rec = (
        "/home/sharf-lab/Desktop/Analysis_shared/data/spikesort_test/"
        "maxtwo_concat_test/baseline/M07653_Control_Baseline_2_19_2026.raw.h5"
    )

    print("=" * 60)
    print("Inversion 1: impossibly large min-free-inter-gb (expect WARN)")
    print("=" * 60)
    cfg = SortingPipelineConfig.from_kwargs(
        stream_id="well000",
        hdf5_plugin_path="/home/sharf-lab/MaxLab/so",
        freq_min=300,
        freq_max=3000,
        use_docker=True,
    )
    cfg.execution.preflight_min_free_inter_gb = 999_999.0
    findings = run_preflight(cfg, [rec], [inter], [res])
    inter_disk = _findings_with_code(findings, "low_disk_inter")
    if not inter_disk:
        print(f"FAIL: no low_disk_inter finding. Got: "
              f"{[f.code for f in findings]}")
        return 1
    f = inter_disk[0]
    if f.level != "warn":
        print(f"FAIL: low_disk_inter level={f.level!r}, expected 'warn'")
        return 1
    print(f"PASS: low_disk_inter [{f.level}] — {f.message[:100]}")

    print("\n" + "=" * 60)
    print("Inversion 2: nonexistent HDF5 plugin path (expect FAIL)")
    print("=" * 60)
    if BAD_PLUGIN.exists():
        shutil.rmtree(BAD_PLUGIN)
    cfg2 = SortingPipelineConfig.from_kwargs(
        stream_id="well000",
        hdf5_plugin_path=str(BAD_PLUGIN),
        freq_min=300,
        freq_max=3000,
        use_docker=True,
    )
    findings2 = run_preflight(cfg2, [rec], [inter], [res])
    plugin_findings = _findings_with_code(findings2, "hdf5_plugin_missing")
    if not plugin_findings:
        print(f"FAIL: no hdf5_plugin_missing finding. Got: "
              f"{[f.code for f in findings2]}")
        return 1
    f = plugin_findings[0]
    if f.level != "fail":
        print(f"FAIL: hdf5_plugin_missing level={f.level!r}, expected 'fail'")
        return 1
    print(f"PASS: hdf5_plugin_missing [{f.level}] — {f.message[:100]}")

    print("\n" + "=" * 60)
    print("Inversion 3: nonexistent recording path (expect FAIL)")
    print("=" * 60)
    cfg3 = SortingPipelineConfig.from_kwargs(
        stream_id="well000",
        hdf5_plugin_path="/home/sharf-lab/MaxLab/so",
        freq_min=300,
        freq_max=3000,
        use_docker=True,
    )
    bogus_rec = "/tmp/this_recording_does_not_exist.raw.h5"
    findings3 = run_preflight(cfg3, [bogus_rec], [inter], [res])
    rec_findings = [
        f for f in findings3
        if f.code in ("recording_missing", "recording_not_found")
        or "missing" in f.code.lower() and "recording" in f.code.lower()
    ]
    if not rec_findings:
        print(f"FAIL: no recording-missing finding. Got: "
              f"{[(f.code, f.level) for f in findings3]}")
        return 1
    f = rec_findings[0]
    if f.level != "fail":
        print(f"FAIL: {f.code} level={f.level!r}, expected 'fail'")
        return 1
    print(f"PASS: {f.code} [{f.level}] — {f.message[:100]}")

    print("\n" + "=" * 60)
    print("Sanity baseline: defaults + real plugin + real recording")
    print("=" * 60)
    cfg4 = SortingPipelineConfig.from_kwargs(
        stream_id="well000",
        hdf5_plugin_path="/home/sharf-lab/MaxLab/so",
        freq_min=300,
        freq_max=3000,
        use_docker=True,
    )
    findings4 = run_preflight(cfg4, [rec], [inter], [res])
    fail_baseline = [f for f in findings4 if f.level == "fail"]
    print(f"Baseline findings: {len(findings4)} total, "
          f"{len(fail_baseline)} fail-level")
    for f in fail_baseline:
        print(f"  fail: {f.code} — {f.message[:100]}")
    if fail_baseline:
        print("WARN: baseline produced fail-level findings — could be a real "
              "host issue, or a test environment artefact.")
    else:
        print("OK: baseline preflight is clean.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
