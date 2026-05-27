"""Shared utility classes and functions for the spike sorting pipeline.

These are used by both ``kilosort2.py`` and ``pipeline.py`` and live
in this separate module to avoid circular imports.
"""

import datetime
import logging
import os
import shutil
import sys
import time
import warnings
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple, Union

import numpy as np

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
# Tier L-F4: spike_sorting/ historically mixed ``print(...)`` (captured
# by ``Tee`` via ``sys.stdout``) and ``_logger.warning(...)`` (default
# ``StreamHandler`` writes to ``sys.stderr``). Watchdog warnings via
# the logger never made it into the Tee log file. Standardising on
# logger style requires the loggers to follow whatever ``sys.stdout``
# currently is — the Tee swap, in particular. ``logging.StreamHandler``
# captures the stream at handler-init time, so a plain
# ``StreamHandler(sys.stdout)`` installed at import time would keep
# writing to the *original* stdout even after Tee swaps it.
#
# ``_StdoutFollowingHandler`` resolves ``sys.stdout`` on each emit so
# the handler tracks the active Tee writer transparently.


class _StdoutFollowingHandler(logging.Handler):
    """Logging handler that writes to whichever ``sys.stdout`` is current.

    Unlike ``logging.StreamHandler`` (which captures its target
    stream at handler-init), this resolves ``sys.stdout`` on every
    emit so the Tee context manager's stdout-swap is followed
    automatically.
    """

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            msg = self.format(record)
            stream = sys.stdout
            stream.write(msg + "\n")
            try:
                stream.flush()
            except Exception:
                pass
        except Exception:
            self.handleError(record)


def _configure_spike_sorting_logger() -> None:
    """Wire the stdout-following handler onto ``spikelab.spike_sorting``.

    Idempotent: a second call from another module-load path is a
    no-op. Sets the logger level to INFO so ``_logger.info(...)``
    calls reach the handler. Propagation is left at its default
    (``True``) so test harnesses like pytest's ``caplog`` — which
    attach their handler to the root logger — can still observe
    records from ``spikelab.spike_sorting.*`` modules. Host
    applications that configure their own root handlers will see
    SpikeLab output in their own logs in addition to the Tee
    capture; that's the standard library convention for libraries.
    """
    logger = logging.getLogger("spikelab.spike_sorting")
    if any(isinstance(h, _StdoutFollowingHandler) for h in logger.handlers):
        return
    handler = _StdoutFollowingHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


_configure_spike_sorting_logger()


_logger = logging.getLogger(__name__)


def _check_unit_id_density(
    unit_ids: Sequence[int],
    n_samples: int,
    n_channels: int,
    dtype: Any = np.float32,
) -> None:
    """Guard against OOM from allocating ``(max(unit_ids)+1, T, C)`` caches.

    Callers that index template/waveform caches by raw ``unit_id``
    (rather than by a dense ``0..N-1`` index) size the cache by
    ``max(unit_ids) + 1``. Heavy Phy curation can leave a sparse
    cluster_id space (e.g. ``[0, 1, 47, 50000]``) for which that
    allocation would consume tens of GB for a handful of surviving
    units. Raise a clear ``MemoryError`` before the allocation rather
    than crashing the process.

    Triggers when ``max(unit_ids) > 100 * len(unit_ids)``. Threshold is
    deliberately loose so benign sparseness from light Phy curation
    (e.g. dropping a few clusters) does not false-positive.
    """
    if not len(unit_ids):
        return
    max_uid = int(max(unit_ids))
    n_units = len(unit_ids)
    if max_uid <= 100 * n_units:
        return
    bytes_per_elem = np.dtype(dtype).itemsize
    gb = ((max_uid + 1) * n_samples * n_channels * bytes_per_elem) / (1024**3)
    raise MemoryError(
        f"Unit IDs are pathologically sparse (max={max_uid}, "
        f"n_units={n_units}); allocating a ({max_uid + 1}, {n_samples}, "
        f"{n_channels}) {np.dtype(dtype).name} array would consume "
        f"~{gb:.1f} GB. This typically results from Phy curation that "
        f"drops most clusters but keeps high-numbered ones. Pass "
        f"compact=True to KilosortSortingExtractor (or compact your "
        f"unit IDs upstream) to use a dense unit_id space."
    )


def get_system_ram_bytes() -> Optional[int]:
    """Return total system physical RAM in bytes, or None if unavailable.

    Tries POSIX ``os.sysconf`` first, then ``psutil`` if installed,
    then the Windows ``GlobalMemoryStatusEx`` API via ctypes.

    Returns:
        ram_bytes (int or None): Total physical RAM in bytes, or None
            if no detection method succeeds.
    """
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        pass

    try:
        import psutil

        return int(psutil.virtual_memory().total)
    except ImportError:
        pass

    if sys.platform == "win32":
        try:
            import ctypes

            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return int(stat.ullTotalPhys)
        except Exception:
            pass

    return None


#: Width of the banner produced by :func:`print_stage`, in characters.
#: The Tee-log parser in ``report.py`` keys its banner-line regex
#: (``_BANNER_LINE_RE = re.compile(r"^=+$")``) and centered-text regex
#: (``_BANNER_TEXT_RE``) off this value, so the two must agree. Both
#: live in the same package; keep them in sync via this constant.
BANNER_WIDTH = 70

#: Character used to frame the banner. ``report.py``'s parser regex
#: (``_BANNER_LINE_RE``) hard-codes ``=`` to match, so changing this
#: requires updating the parser regex too.
BANNER_CHAR = "="


def print_stage(text: Any) -> None:
    """Print a centered banner message framed by ``=`` lines.

    Parameters:
        text: Message to display (converted to string if not already).
    """
    text = str(text)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    indent = int((BANNER_WIDTH - len(text)) / 2)

    _logger.info("\n" + BANNER_WIDTH * BANNER_CHAR)
    _logger.info(indent * " " + text)
    _logger.info(f"  [{timestamp}]".center(BANNER_WIDTH))
    _logger.info(BANNER_WIDTH * BANNER_CHAR)


class Stopwatch:
    """Simple wall-clock timer for logging pipeline stage durations.

    Parameters:
        start_msg (str or None): Optional message printed when the timer
            starts. When *None*, nothing is printed on construction.
        use_print_stage (bool): If True (default), format *start_msg*
            with the ``print_stage`` banner; otherwise use plain ``print``.
    """

    def __init__(
        self, start_msg: Optional[str] = None, use_print_stage: bool = True
    ) -> None:
        if start_msg is not None:
            if use_print_stage:
                print_stage(start_msg)
            else:
                _logger.info(start_msg)

        self._time_start = time.time()

    def log_time(self, text: Optional[str] = None) -> None:
        if text is None:
            _logger.info(f"Time: {time.time() - self._time_start:.2f}s")
        else:
            _logger.info(f"{text} Time: {time.time() - self._time_start:.2f}s")


class _TeeWriter:
    """File-like wrapper that mirrors writes to both a file and stdout.

    Internal helper for :class:`Tee`. Encapsulates the dual-write
    behaviour as an explicit class with a public ``write`` method,
    replacing the prior ``types.MethodType`` monkey-patch on the
    file object.

    Contract:
      - Every ``write(s)`` writes ``s`` to the underlying file
        verbatim.
      - When ``mirror_to_stdout`` is True, ``s`` is also forwarded
        verbatim to the original stdout via ``stdout.write(s)`` —
        not via ``print``. Tier L-C3: the previous implementation
        used ``print(s, file=self.stdout)`` which appended an extra
        newline; that forced a whitespace-skip hack (``s != "\\n"
        and s != " "``) to avoid double-newlines from ``print("x")``
        (which calls ``write("x")`` then ``write("\\n")``). The
        skip also dropped bare-space writes from ``print("a", "b")``
        — so the mirror saw ``"ab"`` while the file saw ``"a b"``.
        Writing verbatim removes both the double-newline AND the
        a-vs-b-divergence bug.
      - ``fileno()`` and ``isatty()`` delegate to ``self.stdout``.
        Code that checks ``sys.stdout.isatty()`` (Click for colour
        decisions) or ``sys.stdout.fileno()`` (IPython, subprocess
        redirection) used to ``AttributeError`` while Tee was
        active; the delegating methods restore compatibility.

    The ``mirror_to_stdout`` flag is toggled off by :class:`Tee`'s
    exit path so traceback writes go to the log file only, not to
    a possibly-defunct stdout.
    """

    def __init__(self, file_path: Union[str, Path], file_mode: str) -> None:
        self._file = open(file_path, file_mode)
        # Plain attribute (not a property) so existing tests + callers
        # can swap in a mock stdout for verification.
        self.stdout = sys.stdout
        self.mirror_to_stdout = True

    def write(self, s: str) -> None:
        self._file.write(s)
        if self.mirror_to_stdout:
            self.stdout.write(s)

    def flush(self) -> None:
        self._file.flush()
        if self.mirror_to_stdout:
            self.stdout.flush()

    def close(self) -> None:
        self._file.close()

    def fileno(self) -> int:
        """Return the underlying stdout's file descriptor.

        Click, IPython, and ``subprocess.run(..., stdout=...)`` all
        expect file-like objects to expose ``fileno()``. Without
        this delegating method, those callers ``AttributeError``
        while Tee is active.
        """
        return self.stdout.fileno()

    def isatty(self) -> bool:
        """Return whether the underlying stdout is a TTY.

        Click checks this to decide whether to emit ANSI colour
        codes. Delegating to ``self.stdout`` preserves the host
        terminal's tty-ness even when Tee is wrapping the output.
        """
        return self.stdout.isatty()


class Tee:
    """Context manager that mirrors ``stdout`` to a log file.

    While the context is active, every ``print`` call writes to both the
    original ``stdout`` and the specified file. On exit, ``stdout`` is
    restored and the file is closed. Exceptions raised inside the
    context are written to the log before re-raising.

    Parameters:
        file_path (str or Path): Path to the log file.
        file_mode (str): File open mode (e.g. ``'w'`` or ``'a'``).
    """

    def __init__(self, file_path: Union[str, Path], file_mode: str = "a") -> None:
        self._writer = _TeeWriter(file_path, file_mode)

    def __enter__(self) -> Any:
        sys.stdout = self._writer
        return self._writer

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        import traceback

        if exc_type:
            # Disable stdout mirror for traceback output — the original
            # behaviour was to restore ``_file.write`` to the unwrapped
            # ``file_write`` so traceback lines went to the file only.
            self._writer.mirror_to_stdout = False
            _logger.info("Traceback (most recent call last):")
            traceback.print_tb(exc_tb, file=self._writer)
            _logger.info(f"{exc_type.__name__}: {exc_val}")
        sys.stdout = self._writer.stdout  # original stdout captured at __init__
        self._writer.close()


def create_folder(folder: Union[str, Path], parents: bool = True) -> None:
    """Create a directory if it does not already exist.

    Parameters:
        folder (str or Path): Directory path to create.
        parents (bool): Create parent directories as needed (default True).
    """
    folder = Path(folder)
    if not folder.exists():
        folder.mkdir(parents=parents)
        _logger.info(f"Created folder: {folder}")


def delete_folder(folder: Union[str, Path]) -> None:
    """Delete a file or directory tree if it exists.

    Parameters:
        folder (str or Path): Path to the file or directory to delete.
    """
    folder = Path(folder)
    if folder.exists():
        if folder.is_dir():
            shutil.rmtree(folder)
            _logger.info(f"Deleted folder: {folder}")
        else:
            folder.unlink()
            _logger.info(f"Deleted file: {folder}")


def get_paths(
    rec_path: Any,
    inter_path: Any,
    results_path: Any,
    execution_config: Any = None,
    sorter_name: Optional[str] = None,
) -> Tuple[Path, Path, Path, Path, Path, Path, Path, Path, Path]:
    """Resolve and prepare all directory paths for one recording run.

    Derives paths for the binary ``.dat`` file, sorter output,
    waveforms, curation stages, and final results.  Optionally deletes
    stale intermediate folders based on ``execution_config`` recompute
    flags.

    Parameters:
        rec_path (str or Path): Path to the recording file.
        inter_path (str or Path): Root intermediate directory.
        results_path (str or Path): Root results directory.
        execution_config (ExecutionConfig or None): When provided, its
            ``recompute_*`` flags control which intermediate folders
            are deleted before running.
        sorter_name (str or None): Name of the configured sorter.
            Controls the sorter output folder name
            (``{sorter_name}_results``). When ``None``, falls back to
            the legacy ``"kilosort2_results"`` and emits a
            ``DeprecationWarning`` so callers update; passing the
            configured ``config.sorter.sorter_name`` keeps caches
            from different sorters from silently colliding in a
            shared ``kilosort2_results/`` folder.

    Returns:
        tuple: ``(rec_path, inter_path, recording_dat_path,
            output_folder, waveforms_root_folder,
            curation_initial_folder, curation_first_folder,
            curation_second_folder, results_path)`` as ``Path`` objects.
    """
    print_stage("PROCESSING RECORDING")
    _logger.info(f"Recording path: {rec_path}")
    _logger.info(f"Intermediate results path: {inter_path}")
    _logger.info(f"Compiled results path: {results_path}")

    rec_path = Path(rec_path)
    # Path.stem strips only the final suffix, preserving interior dots —
    # so "my.session1.h5" yields "my.session1" rather than "my", which
    # would silently collide with "my.session2.h5" intermediate files.
    rec_name = rec_path.stem

    inter_path = Path(inter_path)

    recording_dat_path = inter_path / (rec_name + "_scaled_filtered.dat")
    if sorter_name is None:
        warnings.warn(
            "get_paths called without sorter_name; defaulting to "
            "'kilosort2_results'. Pass sorter_name=config.sorter.sorter_name "
            "to avoid cross-sorter cache collisions.",
            DeprecationWarning,
            stacklevel=2,
        )
        sorter_name = "kilosort2"
    output_folder = inter_path / f"{sorter_name}_results"

    waveforms_root_folder = inter_path / "waveforms"
    curation_folder = inter_path / "curation"
    curation_initial_folder = curation_folder / "initial"
    curation_first_folder = curation_folder / "first"
    curation_second_folder = curation_folder / "second"

    results_path = Path(results_path)

    if results_path == inter_path:
        results_path /= "results"

    # Delete stale intermediate folders based on recompute flags
    if execution_config is not None:
        exe = execution_config
        delete_folders = []
        if exe.recompute_recording:
            delete_folders.extend(
                (
                    recording_dat_path,
                    output_folder,
                    waveforms_root_folder,
                    curation_folder,
                )
            )
        if exe.recompute_sorting:
            delete_folders.extend((output_folder, waveforms_root_folder))
        if exe.reextract_waveforms:
            delete_folders.append(waveforms_root_folder)
            delete_folders.append(curation_folder)
        if exe.recurate_first:
            delete_folders.append(curation_first_folder)
            delete_folders.append(curation_second_folder)
        if exe.recurate_second:
            delete_folders.append(curation_second_folder)
        for folder in delete_folders:
            delete_folder(folder)

    create_folder(inter_path)
    return (
        rec_path,
        inter_path,
        recording_dat_path,
        output_folder,
        waveforms_root_folder,
        curation_initial_folder,
        curation_first_folder,
        curation_second_folder,
        results_path,
    )
