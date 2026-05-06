"""Disk I/O stall watchdog.

Network-mounted recordings (S3-fuse, NFS, SMB) can hang at the
kernel level while still accepting file handles. The :class:`IOStallWatchdog`
polls ``psutil.disk_io_counters()`` for the volume holding the
intermediate folder; when read+write byte counters stop changing
for the configured tolerance window, it trips just like the other
watchdogs.

This complements but does not replace the inactivity watchdog —
the inactivity watchdog tracks log-file mtime, which catches
sorters that go silent. The I/O stall watchdog catches sorters
that keep printing while waiting for hung kernel I/O.

Detection requires ``psutil``. On platforms or filesystems where
``disk_io_counters(perdisk=True)`` does not expose the relevant
device, the watchdog reports as disabled and yields a no-op.
"""

from __future__ import annotations

import contextvars
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .._exceptions import IOStallError
from ._audit import append_audit_event

_logger = logging.getLogger(__name__)

_active_io_stall_watchdog: contextvars.ContextVar[Optional["IOStallWatchdog"]] = (
    contextvars.ContextVar("active_io_stall_watchdog", default=None)
)


def get_active_io_stall_watchdog() -> Optional["IOStallWatchdog"]:
    """Return the I/O stall watchdog active for the current context, or None.

    Mirror of :func:`._watchdog.get_active_watchdog`. Lets the
    per-recording :class:`KeyboardInterrupt` catch site discover a
    tripped I/O stall watchdog and convert the interrupt into the
    appropriate :class:`IOStallError` rather than letting the raw
    interrupt bubble up.

    Returns:
        watchdog (IOStallWatchdog or None): The active instance, or
            ``None`` when no I/O stall watchdog is currently
            running.
    """
    return _active_io_stall_watchdog.get()


def _resolve_device_for_path(path: Path) -> Optional[str]:
    """Return the device (e.g. ``"sda1"`` / ``"C:"``) for *path*.

    Best-effort: ``psutil.disk_partitions`` to find the longest
    mountpoint prefix. Returns ``None`` when no match.
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception:
        return None
    best: Optional[Tuple[int, str]] = None
    target = str(Path(path).resolve()).lower()
    for part in partitions:
        mp = str(Path(part.mountpoint).resolve()).lower()
        if not mp:
            continue
        if target == mp or target.startswith(
            mp.rstrip("/\\") + ("/" if "/" in mp else "\\")
        ):
            length = len(mp)
            if best is None or length > best[0]:
                best = (length, part.device)
    if best is None:
        return None
    dev = best[1]
    # Map ``part.device`` to the key shape psutil's
    # ``disk_io_counters(perdisk=True)`` uses on each platform.
    # Windows: ``part.device`` is ``"C:\\"`` and the perdisk keys are
    # things like ``"C:"`` or ``"PhysicalDrive0"``. Strip the trailing
    # ``\\`` so the colon-suffixed drive form matches.
    # POSIX: ``part.device`` is ``"/dev/sda1"`` and the perdisk keys
    # are the basename (``"sda1"``).
    if sys.platform == "win32":
        return dev.rstrip("\\") if dev.endswith(":\\") else dev
    return dev.rsplit("/", 1)[-1]


def _read_io_bytes(device: str) -> Optional[int]:
    """Return read+write byte total for *device* via psutil.

    Returns ``None`` when the device cannot be found in
    ``disk_io_counters(perdisk=True)``.

    The fallback lookup handles Windows / POSIX device-name shape
    differences. ``psutil.disk_partitions()`` and
    ``disk_io_counters(perdisk=True)`` use slightly different
    conventions for the same device on Windows, so we try two
    normalisations after the direct lookup misses.

    Examples:
        Windows direct match::

            device = "C:"
            counters = {"C:": <iostat>, ...}
            # Direct lookup hits; no fallback needed.

        Windows fallback via ``device + ":"``::

            device = "C"  # caller resolved it without the colon
            counters = {"C:": <iostat>, ...}
            # Direct lookup misses; fallback ``"C" + ":"`` matches.

        Windows fallback via ``rstrip(":")``::

            device = "C:"  # caller has the colon
            counters = {"C": <iostat>, ...}
            # Direct lookup misses; fallback ``"C:".rstrip(":")``
            # → ``"C"`` matches.

        POSIX direct match::

            device = "sda1"
            counters = {"sda1": <iostat>, "sda": <iostat>, ...}
            # Direct lookup hits the partition entry.
    """
    try:
        import psutil
    except ImportError:
        return None
    try:
        counters = psutil.disk_io_counters(perdisk=True)
    except Exception:
        return None
    if counters is None:
        return None
    info = counters.get(device)
    if info is None:
        # Try without trailing colon (Windows) or with sda partition
        # stripped (Linux: ``sda1`` may aggregate under ``sda``).
        for key in (
            device.rstrip(":"),
            device + ":",
        ):
            if key in counters:
                info = counters[key]
                break
    if info is None:
        return None
    try:
        return int(info.read_bytes) + int(info.write_bytes)
    except Exception:
        return None


class IOStallWatchdog:
    """Daemon-thread watchdog that aborts the sort on disk I/O stalls.

    Use as a context manager around the per-recording sort. Polls
    ``read_bytes + write_bytes`` for the volume holding *folder*
    every ``poll_interval_s``. If the counter does not change for
    ``stall_s`` seconds, builds an :class:`IOStallError`,
    terminates registered subprocesses, runs kill callbacks, and
    raises into the main thread via ``_thread.interrupt_main``.

    Parameters:
        folder (Path): A path on the volume to monitor (typically
            the per-recording intermediate folder).
        stall_s (float): Inactivity tolerance for the byte
            counter, in seconds. Defaults to ``300`` (5 min) —
            long enough to span normal write bursts and quiet
            stretches, short enough to flag genuinely hung mounts.
        poll_interval_s (float): Seconds between polls. Defaults
            to ``10.0``.
        warn_repeat_s (float): Minimum seconds between repeated
            warnings.
        kill_grace_s (float): Seconds between ``terminate()`` and
            ``kill()`` for registered subprocesses.

    Notes:
        - Disabled (no-op) when ``psutil`` is missing or when no
          device can be resolved for *folder*. To skip the I/O-stall
          check intentionally, omit any ``register_kill_callback``
          calls — the watchdog still polls but has nothing to abort.
        - Unlike :class:`HostMemoryWatchdog`, this watchdog does not
          accept subprocess registrations — only kill callbacks. A
          Docker-backed sort whose container is registered with the
          host watchdog will not have its container killed when the
          I/O stall watchdog trips. Callers should rely on the
          per-recording watchdog hierarchy (host watchdog kills
          subprocesses; I/O stall fires callbacks for in-process
          sorters) rather than expecting symmetric subprocess
          teardown across all watchdogs.
    """

    def __init__(
        self,
        folder: Path,
        *,
        stall_s: float = 300.0,
        poll_interval_s: float = 10.0,
        warn_repeat_s: float = 60.0,
        kill_grace_s: float = 5.0,
    ) -> None:
        if stall_s <= 0.0:
            raise ValueError(f"stall_s must be positive, got {stall_s}.")
        if poll_interval_s <= 0.0:
            raise ValueError(
                f"poll_interval_s must be positive, got {poll_interval_s}."
            )
        if kill_grace_s < 0.0:
            raise ValueError(f"kill_grace_s must be non-negative, got {kill_grace_s}.")
        self.folder = Path(folder)
        self.stall_s = float(stall_s)
        self.poll_interval_s = float(poll_interval_s)
        self.warn_repeat_s = float(warn_repeat_s)
        self.kill_grace_s = float(kill_grace_s)

        self._subprocesses: List[Tuple[object, float]] = []
        self._kill_callbacks: List[Callable[[], None]] = []
        self._lock = threading.Lock()

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._tripped = False
        self._stall_at_trip: Optional[float] = None
        self._device: Optional[str] = None
        self._enabled = False
        self._token: Optional[contextvars.Token] = None
        # Set True when the trip cascade ran but
        # ``_thread.interrupt_main`` raised — see
        # :meth:`interrupt_delivery_failed`.
        self._interrupt_main_failed = False

    # ------------------------------------------------------------------
    # Trip-state queries
    # ------------------------------------------------------------------

    def tripped(self) -> bool:
        """Return True once the watchdog has fired its abort path."""
        return self._tripped

    def interrupt_delivery_failed(self) -> bool:
        """Return True if the trip fired but ``_thread.interrupt_main`` raised.

        When True, host I/O protection ran successfully (kill
        callbacks invoked) but the main thread did not receive a
        ``KeyboardInterrupt``. The pipeline's catch site checks this
        to reclassify a downstream exception.

        Returns:
            failed (bool): True only when the watchdog tripped and
                the interrupt delivery raised.
        """
        return self._interrupt_main_failed

    def device(self) -> Optional[str]:
        """Return the resolved device identifier (e.g. "sda1")."""
        return self._device

    def make_error(self, message: Optional[str] = None) -> IOStallError:
        """Build an :class:`IOStallError` from the trip state."""
        if message is None:
            stall = self._stall_at_trip
            stall_str = f"{stall:.1f}" if stall is not None else "?"
            message = (
                f"I/O on device {self._device!r} stalled for {stall_str}s "
                f"(tolerance: {self.stall_s:.1f}s). Likely a hung "
                "network mount or unresponsive storage. Sort aborted."
            )
        return IOStallError(
            message,
            device=self._device,
            stall_s=self.stall_s,
        )

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_kill_callback(self, callback: Callable[[], None]) -> None:
        """Track a zero-arg callable to invoke on watchdog abort."""
        with self._lock:
            self._kill_callbacks.append(callback)

    def unregister_kill_callback(self, callback: Callable[[], None]) -> None:
        with self._lock:
            self._kill_callbacks = [
                c for c in self._kill_callbacks if c is not callback
            ]

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "IOStallWatchdog":
        device = _resolve_device_for_path(self.folder)
        if device is None:
            _logger.warning(
                "could not resolve a block device for %s (psutil "
                "missing or no matching mountpoint) — disabled. The "
                "log inactivity watchdog still covers most stall cases.",
                self.folder,
            )
            self._enabled = False
            return self
        # Probe once to confirm we can read counters for the device.
        if _read_io_bytes(device) is None:
            _logger.warning(
                "device %r is not exposed by "
                "psutil.disk_io_counters(perdisk=True) — disabled. "
                "Common on Linux NVMe setups where only the parent "
                "disk is reported; consider monitoring at the parent "
                "device instead.",
                device,
            )
            self._enabled = False
            return self
        self._device = device
        self._enabled = True
        # Publish the active watchdog so the per-recording
        # ``KeyboardInterrupt`` catch site can convert a
        # ``_thread.interrupt_main`` from this watchdog into a
        # classified ``IOStallError`` rather than letting it
        # bubble up raw.
        self._token = _active_io_stall_watchdog.set(self)
        _logger.info(
            "active: device=%s folder=%s stall_s=%.1f poll=%.1fs",
            device,
            self.folder,
            self.stall_s,
            self.poll_interval_s,
        )
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name=f"IOStallWatchdog[{device}]",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.poll_interval_s + 1.0)
            self._thread = None
        if self._token is not None:
            try:
                _active_io_stall_watchdog.reset(self._token)
            except (LookupError, ValueError, RuntimeError):
                # Another context modified the var between set/reset,
                # or the token was already consumed (Python 3.10+
                # raises RuntimeError on re-used tokens).
                pass
            self._token = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """Polling loop: warn, then trip, then exit."""
        if self._stop_event.wait(self.poll_interval_s):
            return
        last_bytes = _read_io_bytes(self._device or "")
        last_change_t = time.time()
        last_warn_t = 0.0
        blind_started_t: Optional[float] = None
        blind_warned = False
        while not self._stop_event.is_set():
            current = _read_io_bytes(self._device or "")
            now = time.time()
            if current is None:
                # Counters unreadable this poll. Reset last_change_t so
                # we don't accumulate stall time we can't observe; track
                # how long we have been blind so we can warn once.
                last_change_t = now
                if blind_started_t is None:
                    blind_started_t = now
                elif not blind_warned and now - blind_started_t >= self.stall_s:
                    self._warn_blind(now - blind_started_t)
                    blind_warned = True
                self._stop_event.wait(self.poll_interval_s)
                continue
            # Successful read clears the blindness tracker so a later
            # episode is reported afresh.
            blind_started_t = None
            blind_warned = False
            if last_bytes is None:
                last_bytes = current
                last_change_t = now
            elif current != last_bytes:
                last_bytes = current
                last_change_t = now
            stalled_for = now - last_change_t
            if stalled_for >= self.stall_s:
                self._on_trip(stalled_for)
                return
            if (
                stalled_for >= self.stall_s * 0.5
                and now - last_warn_t >= self.warn_repeat_s
            ):
                last_warn_t = now
                self._maybe_warn(stalled_for)
            self._stop_event.wait(self.poll_interval_s)

    def _maybe_warn(self, stalled_for: float) -> None:
        _logger.warning(
            "device %r idle for %.1fs (will abort at %.1fs).",
            self._device,
            stalled_for,
            self.stall_s,
        )
        append_audit_event(
            watchdog="io_stall",
            event="warn",
            device=self._device,
            stalled_for_s=stalled_for,
            tolerance_s=self.stall_s,
        )

    def _warn_blind(self, blind_for: float) -> None:
        _logger.warning(
            "I/O counter for device %r unreadable for %.1fs — "
            "watchdog is blind to stalls until counters become "
            "readable again. Other watchdogs (log inactivity, host "
            "memory) still apply.",
            self._device,
            blind_for,
        )
        append_audit_event(
            watchdog="io_stall",
            event="blind_warn",
            device=self._device,
            blind_for_s=blind_for,
            tolerance_s=self.stall_s,
        )

    def _on_trip(self, stalled_for: float) -> None:
        self._tripped = True
        self._stall_at_trip = stalled_for
        _logger.error(
            "TRIP: device %r stalled for %.1fs (>= %.1fs). Aborting sort.",
            self._device,
            stalled_for,
            self.stall_s,
        )
        append_audit_event(
            watchdog="io_stall",
            event="abort",
            device=self._device,
            stalled_for_s=stalled_for,
            tolerance_s=self.stall_s,
        )
        with self._lock:
            callbacks = list(self._kill_callbacks)
        for cb in callbacks:
            try:
                cb()
            except (SystemExit, KeyboardInterrupt):
                # An in-process kill callback delivers KeyboardInterrupt
                # via _thread.interrupt_main(); SystemExit signals
                # operator-requested abort. Both must propagate.
                raise
            except Exception as exc:
                _logger.error("kill_callback raised: %r; continuing.", exc)
        # If __exit__ ran while we were mid-cascade (callbacks can
        # take several seconds), the with-block has already torn
        # down. Sending interrupt_main() now would land a phantom
        # KeyboardInterrupt in whatever code is running next — the
        # next sort, an exception handler, or the interactive
        # prompt. Skip it.
        if self._stop_event.is_set():
            _logger.info("suppressing interrupt_main: watchdog is already exiting.")
            return
        try:
            import _thread as _t

            _t.interrupt_main()
        except Exception as exc:
            self._interrupt_main_failed = True
            _logger.error("failed to interrupt main: %s", exc)
            append_audit_event(
                watchdog="io_stall",
                event="interrupt_delivery_failed",
                device=self._device,
                error=repr(exc),
            )
