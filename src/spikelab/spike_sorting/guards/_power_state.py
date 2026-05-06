"""Cross-platform: prevent the system from sleeping during a sort.

A laptop closing its lid mid-sort, or the OS kicking off a
"modern standby" / suspend cycle, can suspend the whole sort
process. Resume is often incomplete (CUDA contexts lost, file
handles invalidated, network mounts stale). The cleanest fix is
to ask the OS not to sleep while the sort is running.

Per-platform mechanism:

* Windows — ``SetThreadExecutionState(ES_CONTINUOUS |
  ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)``. Released on
  context exit by clearing the flags.
* macOS — spawns ``caffeinate -dims`` as a child process.
  ``-d`` prevents display sleep, ``-i`` prevents idle sleep,
  ``-m`` prevents disk idle, ``-s`` blocks system sleep on AC
  power. The child is terminated on context exit.
* Linux — spawns ``systemd-inhibit --what=sleep:idle ...
  sleep infinity`` as a child process; the inhibitor lock
  releases when the child exits. Requires systemd; non-systemd
  inits fall back to no-op with a one-line notice.

In every case the context manager is best-effort: when the
required tool is missing the context yields ``False`` rather than
raising.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import sys
from typing import Iterator, Optional

_logger = logging.getLogger(__name__)

# Windows API constants — defined here to avoid the ctypes import
# on non-Windows hosts.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_AWAYMODE_REQUIRED = 0x00000040  # avoid screensaver suspend on desktops


@contextlib.contextmanager
def prevent_system_sleep() -> Iterator[bool]:
    """Ask the OS not to enter sleep / hibernate during the context.

    Cross-platform: uses ``SetThreadExecutionState`` on Windows,
    ``caffeinate`` on macOS, and ``systemd-inhibit`` on Linux. On
    every other platform the context manager is a no-op and yields
    ``False``.

    Yields:
        active (bool): ``True`` when the safeguard is engaged,
            ``False`` when it is unavailable on this platform or
            could not be installed.

    Notes:
        - macOS / Linux variants spawn a child process that holds
          the inhibitor lock; the child is terminated on context
          exit so the lock releases promptly.
        - The display may still blank under all backends — only
          *system* sleep is prevented, not display blanking.
    """
    if sys.platform == "win32":
        yield from _prevent_sleep_windows()
        return
    if sys.platform == "darwin":
        yield from _prevent_sleep_macos()
        return
    if sys.platform.startswith("linux"):
        yield from _prevent_sleep_linux()
        return
    yield False


def _prevent_sleep_windows() -> Iterator[bool]:
    try:
        import ctypes  # noqa: WPS433
    except Exception:
        yield False
        return

    flags = _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_AWAYMODE_REQUIRED

    # Bind kernel32 explicitly to None before the inner try so the
    # outer finally never references an unbound name. Previously the
    # finally relied on a broad ``except Exception`` to mask a
    # potential ``UnboundLocalError`` if ``ctypes.windll.kernel32``
    # raised before assignment (ctypes stub on WSL/Wine).
    kernel32 = None
    active = False
    try:
        kernel32 = ctypes.windll.kernel32
        prev = kernel32.SetThreadExecutionState(flags)
        if prev == 0:
            # MSDN: SetThreadExecutionState returns 0 on failure.
            # Previously we logged a warning but yielded True, which
            # told the operator "sleep prevented" when it wasn't.
            # Yield False so the caller's status check matches reality.
            _logger.warning(
                "SetThreadExecutionState returned 0; sleep prevention "
                "did NOT take effect. Yielding False so the caller "
                "knows the OS rejected the request."
            )
            active = False
        else:
            _logger.info("active (Windows): system sleep prevented.")
            active = True
    except Exception as exc:
        _logger.warning("failed to engage sleep prevention: %r", exc)
        yield False
        return

    try:
        yield active
    finally:
        if kernel32 is not None and active:
            # Only attempt the clear if we successfully engaged in
            # the first place. If the original call returned 0,
            # there is nothing to clear and a redundant call risks
            # the same failure mode.
            try:
                kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
            except Exception:
                pass


def _spawn_inhibitor(argv: list, label: str) -> Optional[subprocess.Popen]:
    """Spawn an inhibitor child or return ``None`` on failure."""
    try:
        return subprocess.Popen(
            argv,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        _logger.warning("%s not found; sleep prevention unavailable.", label)
        return None
    except Exception as exc:
        _logger.warning("failed to spawn %s: %r", label, exc)
        return None


def _terminate_inhibitor(proc: subprocess.Popen, label: str) -> None:
    """Best-effort terminate-then-kill of the inhibitor child."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
    except Exception:
        pass


def _prevent_sleep_macos() -> Iterator[bool]:
    proc = _spawn_inhibitor(
        ["caffeinate", "-dims"],
        "caffeinate",
    )
    if proc is None:
        yield False
        return
    _logger.info("active (macOS): caffeinate -dims engaged.")
    try:
        yield True
    finally:
        _terminate_inhibitor(proc, "caffeinate")


# Linux inhibitor candidates tried in order. ``systemd-inhibit`` is
# preferred (most precise: blocks specifically sleep/idle); on
# non-systemd inits we fall back to softer tools that prevent
# screensaver / display sleep at the session level. None of the
# fallbacks blocks system suspend with the precision of
# ``systemd-inhibit``, but each prevents the most common
# sort-killer scenarios on its respective stack.
_LINUX_INHIBITOR_CANDIDATES = (
    (
        [
            "systemd-inhibit",
            "--what=sleep:idle",
            "--who=spikelab",
            "--why=spike sorting in progress",
            "--mode=block",
            "sleep",
            "infinity",
        ],
        "systemd-inhibit",
    ),
    (
        [
            "xdg-screensaver",
            "suspend",
            # xdg-screensaver requires an X11 window ID. It accepts
            # the literal "0" to refer to the root window when a
            # session is present; on headless / Wayland-only hosts
            # the spawn just exits with a non-zero status which we
            # treat as "no inhibitor available".
            "0",
        ],
        "xdg-screensaver",
    ),
    (
        [
            "dbus-send",
            "--session",
            "--dest=org.freedesktop.ScreenSaver",
            "--type=method_call",
            "/org/freedesktop/ScreenSaver",
            "org.freedesktop.ScreenSaver.SimulateUserActivity",
        ],
        "dbus-send",
    ),
)


def _prevent_sleep_linux() -> Iterator[bool]:
    # ``systemd-inhibit`` holds the lock for the lifetime of its
    # child command; ``sleep infinity`` is the canonical "stay
    # alive until killed" placeholder. On non-systemd Linux we
    # walk the candidate list above until one spawns successfully.
    proc = None
    label = ""
    for argv, candidate_label in _LINUX_INHIBITOR_CANDIDATES:
        spawned = _spawn_inhibitor(argv, candidate_label)
        if spawned is not None:
            proc = spawned
            label = candidate_label
            break

    if proc is None:
        yield False
        return
    _logger.info("active (Linux): %s engaged.", label)
    try:
        yield True
    finally:
        _terminate_inhibitor(proc, label)
