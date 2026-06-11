"""Stress test #6: ``find_tripped_global_watchdog`` honours documented
priority order.

When multiple watchdogs trip simultaneously (e.g. host RAM
exhaustion cascading into GPU writes stalling and disk swap I/O
stalling), :func:`find_tripped_global_watchdog` must report the
**root cause** first. The documented order is:

    host → gpu → io_stall

ranked by likelihood of being the root cause. A misordering would
make the operator chase a downstream symptom instead of the actual
cause when reading classified failure reports.

This invariant is documented in the function's docstring but
nothing currently asserts it end-to-end. The test publishes stub
watchdogs to each ContextVar, varies which ones report ``tripped``,
and verifies the resolution.
"""
import sys
from typing import Optional

from spikelab.spike_sorting.guards import (
    find_tripped_global_watchdog,
)
from spikelab.spike_sorting.guards._gpu_watchdog import _active_gpu_watchdog
from spikelab.spike_sorting.guards._io_stall import _active_io_stall_watchdog
from spikelab.spike_sorting.guards._watchdog import _active_watchdog


class _StubWatchdog:
    """Minimal duck-typed watchdog that just answers ``tripped()``."""

    def __init__(self, name: str, is_tripped: bool) -> None:
        self.name = name
        self._is_tripped = is_tripped

    def tripped(self) -> bool:
        return self._is_tripped

    def __repr__(self) -> str:
        state = "TRIPPED" if self._is_tripped else "ok"
        return f"<{self.name} {state}>"


def _set_watchdogs(
    host: Optional[_StubWatchdog],
    gpu: Optional[_StubWatchdog],
    io: Optional[_StubWatchdog],
) -> tuple:
    """Publish three stubs to the ContextVars; return reset tokens."""
    return (
        _active_watchdog.set(host),
        _active_gpu_watchdog.set(gpu),
        _active_io_stall_watchdog.set(io),
    )


def _reset_watchdogs(tokens) -> None:
    host_tok, gpu_tok, io_tok = tokens
    _active_io_stall_watchdog.reset(io_tok)
    _active_gpu_watchdog.reset(gpu_tok)
    _active_watchdog.reset(host_tok)


def _check(label: str, host, gpu, io, expected_name: Optional[str]) -> bool:
    """Set the three stubs, call the resolver, restore, compare."""
    tokens = _set_watchdogs(host, gpu, io)
    try:
        result = find_tripped_global_watchdog()
    finally:
        _reset_watchdogs(tokens)
    actual_name = getattr(result, "name", None)
    ok = actual_name == expected_name
    marker = "PASS" if ok else "FAIL"
    expected_repr = expected_name if expected_name is not None else "None"
    print(f"  {marker}  {label}")
    print(f"        host={host!r}  gpu={gpu!r}  io={io!r}")
    print(f"        -> {result!r} (expected {expected_repr})")
    return ok


def main() -> int:
    host_tripped = _StubWatchdog("host", True)
    host_ok = _StubWatchdog("host", False)
    gpu_tripped = _StubWatchdog("gpu", True)
    gpu_ok = _StubWatchdog("gpu", False)
    io_tripped = _StubWatchdog("io", True)
    io_ok = _StubWatchdog("io", False)

    print("=" * 60)
    print("Priority resolution: host → gpu → io_stall")
    print("=" * 60)
    cases = [
        # All three tripped: host wins (root cause)
        ("all-three-tripped", host_tripped, gpu_tripped, io_tripped, "host"),
        # host not present, gpu + io tripped: gpu wins
        ("host-absent-gpu+io-tripped", None, gpu_tripped, io_tripped, "gpu"),
        # host present but ok, gpu + io tripped: gpu wins (host skipped)
        ("host-ok-gpu+io-tripped", host_ok, gpu_tripped, io_tripped, "gpu"),
        # only io tripped
        ("only-io-tripped", host_ok, gpu_ok, io_tripped, "io"),
        # only host tripped
        ("only-host-tripped", host_tripped, gpu_ok, io_ok, "host"),
        # only gpu tripped
        ("only-gpu-tripped", host_ok, gpu_tripped, io_ok, "gpu"),
        # nothing tripped → None
        ("nothing-tripped", host_ok, gpu_ok, io_ok, None),
        # nothing published → None
        ("none-published", None, None, None, None),
        # host tripped wins even if gpu + io also tripped (priority)
        ("host-and-gpu-tripped", host_tripped, gpu_tripped, io_ok, "host"),
        # host tripped wins even if io tripped
        ("host-and-io-tripped", host_tripped, gpu_ok, io_tripped, "host"),
    ]

    n_pass = 0
    for label, host, gpu, io, expected in cases:
        if _check(label, host, gpu, io, expected):
            n_pass += 1
        print()

    print("=" * 60)
    print(f"Results: {n_pass}/{len(cases)} priority cases passed")
    print("=" * 60)
    return 0 if n_pass == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
