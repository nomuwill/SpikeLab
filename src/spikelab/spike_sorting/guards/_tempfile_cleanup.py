"""Best-effort sweep of leaked temp files at sort end.

When a sort crashes mid-flight (os._exit fired by an in-process
inactivity watchdog, MATLAB child killed, RT-Sort numba kernel
deadlocked) Python's normal ``tempfile`` cleanup hooks may not run
and intermediate files in ``$TMPDIR`` / ``%TEMP%`` are left behind.
Across many crashed sorts this can fill the temp volume.

This module records the temp-folder state at sort start and, on
exit, sweeps any new files matching well-known sorter prefixes.
Best-effort — failures (permission errors, files locked by other
processes) are swallowed.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Iterator, List, Set

_logger = logging.getLogger(__name__)

# Filename substrings (NOT regex) that identify temp artefacts as
# belonging to this sort or its sorter children. Conservative list
# to avoid removing unrelated files.
#
# Matching is via ``substring in name.lower()`` so each entry is a
# lowercase substring — not a prefix and not a regex. Implications:
#
# * ``"matlab_temp"`` matches ``MATLAB_TEMP_42.dat``,
#   ``my_matlab_temp_file``, etc. It does NOT match a bare
#   ``"matlab_xyz"`` file produced by Kilosort2's MATLAB process —
#   those would need their own marker.
# * ``"kilosort"`` matches ``"kilosort_cache"``, ``"my_kilosort"``,
#   ``"KILOSORT4_RUN"``. Aggressive enough to catch MEX dumper
#   outputs that prefix the sorter name.
# * Adding a new marker requires both the source list here and any
#   downstream tests that exercise the sweep behaviour.
#
# Verification of this list against actual production temp-file
# artefacts is left as an open SUGGESTION — operators with access
# to crashed-sort tmpdirs should grep the surviving filenames and
# update this tuple to match.
_SORTER_TEMP_MARKERS = (
    "spikelab",
    "kilosort",
    "rt_sort",
    "rtsort",
    "spikeinterface",
    "matlab_temp",
    "mxdumper",
    "tmp_recording_",
)


# Bound the per-marker subtree walk so a pathological temp dir
# state (millions of files inside a marker-named directory) cannot
# hang ``cleanup_temp_files`` at sort end. Mirrors the depth-bounded
# behaviour used by ``_disk_watchdog._top_consumers``.
_MAX_MARKER_SUBTREE_DEPTH: int = 4
_MAX_MARKER_SUBTREE_FILES: int = 10_000


def _list_marker_files(temp_dir: Path) -> Set[Path]:
    """Return the set of files in *temp_dir* whose names match a marker.

    Non-recursive scan at the top level — only direct entries.
    Sorter temp artefacts are typically created at the top of
    ``$TMPDIR``. Subdirectories whose names match a marker are
    walked one extra layer deep with the bounded helper below
    (where mxdumper-style trees can land).

    The per-marker subtree walk is depth-bounded
    (:data:`_MAX_MARKER_SUBTREE_DEPTH`) and file-count-bounded
    (:data:`_MAX_MARKER_SUBTREE_FILES`) so a pathological tree
    cannot stall the cleanup. When either bound is hit a debug log
    is emitted and the walk is truncated; the cleanup is best-effort
    and missing a few deeply-nested files is preferable to hanging
    the sort exit.
    """
    found: Set[Path] = set()
    if not temp_dir.exists():
        return found
    try:
        for entry in temp_dir.iterdir():
            name = entry.name.lower()
            if any(m in name for m in _SORTER_TEMP_MARKERS):
                if entry.is_file():
                    found.add(entry)
                elif entry.is_dir():
                    _walk_marker_subtree(entry, found)
    except OSError:
        return found
    return found


def _walk_marker_subtree(root: Path, found: Set[Path]) -> None:
    """Walk a marker-named subdirectory under bounded depth + file count."""
    base_depth = len(root.parts)
    file_count = 0
    try:
        for dirpath, _dirs, files in os.walk(root, onerror=lambda _e: None):
            depth = len(Path(dirpath).parts) - base_depth
            if depth > _MAX_MARKER_SUBTREE_DEPTH:
                _dirs[:] = []  # prune deeper traversal
                continue
            for name in files:
                p = Path(dirpath) / name
                try:
                    if p.is_file():
                        found.add(p)
                        file_count += 1
                        if file_count >= _MAX_MARKER_SUBTREE_FILES:
                            _logger.debug(
                                "marker subtree %s truncated at %d files",
                                root,
                                file_count,
                            )
                            return
                except OSError:
                    continue
    except Exception:
        # Treat any traversal failure as "we got what we got"; the
        # outer cleanup is best-effort.
        pass


@contextlib.contextmanager
def cleanup_temp_files(enabled: bool = True) -> Iterator[None]:
    """Sweep new sorter-marker temp files created during the context.

    Records the set of marker-matched temp files at entry; on exit,
    deletes any that appeared during the context. Sweeping happens
    only on **clean** ``__exit__`` (no exception propagating out)
    to avoid removing artefacts that the user may want to inspect
    after a failure.

    Best-effort: per-file errors are logged but do not propagate.

    Parameters:
        enabled (bool): When False, the context manager is a no-op.

    Notes:
        - The sweep is non-aggressive: it removes only files
          matching the well-known prefix list, to avoid clobbering
          unrelated workspace temp files.
        - A failed sort intentionally leaves its temp files behind
          so an operator can diagnose. The next successful sort
          will sweep them up.
    """
    if not enabled:
        yield
        return

    try:
        temp_dir = Path(tempfile.gettempdir())
    except Exception:
        yield
        return

    pre_existing = _list_marker_files(temp_dir)

    # Failures inside the with-block propagate naturally; the post-yield
    # sweep below only runs on a clean exit, leaving temp files in
    # place after a failure so the operator can inspect them.
    yield

    # Clean exit — sweep any new marker files.
    try:
        post = _list_marker_files(temp_dir)
        new_files = post - pre_existing
        if not new_files:
            return
        removed = 0
        failed = 0
        for f in new_files:
            try:
                f.unlink()
                removed += 1
            except Exception:
                failed += 1
        if removed > 0 or failed > 0:
            _logger.info(
                "swept %d stale temp file(s) from %s (%d failed).",
                removed,
                temp_dir,
                failed,
            )
    except Exception as exc:
        _logger.warning("sweep failed: %r", exc)
