"""
Splitter-job submission and per-well sorter fanout.

Vendored from SpikeCanvas-EphysPipeline/Services/Spike_Sorting_Listener/src/splitter_fanout.py
with these changes:
  - imports are package-local (kube.Kube, job_utils.*, .s3)
  - braingeneers.utils.s3wrangler.list_objects swapped for our boto3 helper
  - module-private helpers preserved verbatim so behavior matches the listener
"""
import logging
import posixpath
import re
import threading
import time
from urllib.parse import urlparse

from kubernetes import client, config

from . import s3 as wr
from .job_utils import (
    CACHE_S3_BUCKET,
    DEFAULT_S3_BUCKET,
    JOB_PREFIX,
    NAMESPACE,
    format_job_name,
)
from .kube import Kube

SPLITTER_JOB_PREFIX = "edp-ma2split-"


def spawn_splitter_fanout(uuid: str,
                          experiment: str,
                          file_path: str,
                          splitter_cfg: dict,
                          sorter_tpl: dict):
    """Submit splitter Job + start a watcher thread that fans out sorters."""
    logging.info("=== spawn_splitter_fanout called ===")
    logging.info(f"Parameters - UUID: {uuid}, Experiment: {experiment}")

    if not uuid or not experiment or not file_path:
        raise ValueError("UUID, experiment, and file_path must be provided")
    if not splitter_cfg or not sorter_tpl:
        raise ValueError("splitter_cfg and sorter_tpl must be provided")

    required = ["args", "cpu_request", "memory_request", "disk_request", "GPU", "image",
                "init_args", "init_cpu_request", "init_memory_request",
                "init_disk_request", "init_GPU"]
    missing = [f for f in required if f not in splitter_cfg]
    if missing:
        raise ValueError(f"Missing required fields in splitter_cfg: {missing}")

    base_exp = _normalize_experiment_name(experiment)
    split_name = _build_splitter_job_name(uuid, base_exp)

    logging.info(f"Creating splitter job with name: {split_name}")

    cfg = splitter_cfg.copy()
    cfg.update({
        "file_path": file_path,
        "init_container": {
            "name": "maxtwo-download",
            "image": splitter_cfg["image"],
            "args": f"{splitter_cfg['init_args']} {file_path}",
            "cpu_request": splitter_cfg["init_cpu_request"],
            "memory_request": splitter_cfg["init_memory_request"],
            "disk_request": splitter_cfg["init_disk_request"],
            "GPU": splitter_cfg["init_GPU"],
        },
    })

    splitter_job = Kube(split_name, cfg)

    if not splitter_job.check_job_exist():
        logging.info(f"Splitter config: {cfg}")
        result = splitter_job.create_job()
        if result == -1:
            logging.error(f"Failed to create splitter job {split_name}")
            return
        logging.info(f"Splitter Job {split_name} submitted successfully")
        job_created = True
    else:
        logging.info(f"Splitter Job {split_name} already exists")
        job_created = False

    watcher = threading.Thread(
        target=_watch_and_fanout,
        name=f"fanout-{base_exp}",
        args=(split_name, uuid, experiment, file_path, sorter_tpl, job_created),
        daemon=False,
    )
    watcher.start()
    logging.info(f"Started watcher thread for job {split_name}")
    logging.info(f"NOTE: Sorter jobs will be created ONLY after splitter {split_name} succeeds")


# -------------------------------------------------------------------- internals
def _safe_get_job_status(job_name, max_retries: int = 3, retry_delay: int = 5):
    for attempt in range(max_retries):
        try:
            config.load_kube_config()
            api = client.BatchV1Api()
            job = api.read_namespaced_job_status(
                name=job_name, namespace=NAMESPACE, _request_timeout=30
            )
            return job.status
        except Exception as err:
            msg = str(err)
            if "InvalidChunkLength" in msg or "Connection broken" in msg:
                logging.warning(f"Connection error for job {job_name} "
                                f"(attempt {attempt + 1}/{max_retries}): {msg}")
            else:
                logging.warning(f"API error for job {job_name} "
                                f"(attempt {attempt + 1}/{max_retries}): {msg}")
            if attempt == max_retries - 1:
                logging.error(f"Failed to get status for job {job_name} "
                              f"after {max_retries} attempts: {msg}")
                return None
            delay = retry_delay * (attempt + 1)
            logging.info(f"Waiting {delay}s before retry...")
            time.sleep(delay)
    return None


def _watch_and_fanout(split_name, uuid_param, experiment, file_path, tpl, job_created=True):
    try:
        logging.info(f"Starting watcher for splitter job: {split_name} (job_created={job_created})")
        max_wait_time = 7200
        poll_interval = 30
        elapsed = 0
        consecutive_errors = 0
        max_consecutive_errors = 10

        if not job_created:
            try:
                status = _safe_get_job_status(split_name)
                if status is None:
                    logging.error(f"Could not get initial status for {split_name}")
                    return
                if status.succeeded and status.succeeded > 0:
                    logging.info(f"[{split_name}] Already completed -> fan-out sorters")
                    _launch_sorters(uuid_param, experiment, file_path, tpl)
                    return
                if status.failed and status.failed >= 2:
                    logging.error(f"[{split_name}] Already failed; skip sorters")
                    return
                logging.info(f"[{split_name}] Existing job is still running, starting monitoring")
            except Exception as err:
                logging.error(f"Error checking existing job status: {err}")
                logging.info(f"Will proceed with normal monitoring for {split_name}")

        while elapsed < max_wait_time:
            try:
                status = _safe_get_job_status(split_name, max_retries=2, retry_delay=3)
                if status is None:
                    consecutive_errors += 1
                    logging.warning(f"Failed to get job status for {split_name} "
                                    f"(consecutive errors: {consecutive_errors})")
                    if consecutive_errors >= max_consecutive_errors:
                        logging.error(f"Too many consecutive errors "
                                      f"({consecutive_errors}), giving up on {split_name}")
                        return
                    backoff = min(poll_interval * consecutive_errors, 120)
                    logging.info(f"Backing off for {backoff}s due to consecutive errors")
                    time.sleep(backoff)
                    elapsed += backoff
                    continue

                consecutive_errors = 0
                logging.info(f"Job {split_name} status: succeeded={status.succeeded}, "
                             f"failed={status.failed}, active={status.active}")

                if status.succeeded and status.succeeded > 0:
                    logging.info(f"[{split_name}] Succeeded -> fan-out sorters")
                    _launch_sorters(uuid_param, experiment, file_path, tpl)
                    return
                if status.failed and status.failed >= 2:
                    logging.error(f"[{split_name}] Failed {status.failed} times; skip sorters")
                    return

                logging.info(f"[{split_name}] Still running, waiting {poll_interval}s... "
                             f"({elapsed}/{max_wait_time}s elapsed)")
                time.sleep(poll_interval)
                elapsed += poll_interval

            except Exception as err:
                consecutive_errors += 1
                logging.error(f"Unexpected error monitoring {split_name} "
                              f"(attempt {consecutive_errors}): {err}")
                if consecutive_errors >= max_consecutive_errors:
                    logging.error(f"Too many consecutive errors "
                                  f"({consecutive_errors}), giving up on {split_name}")
                    return
                logging.info(f"Waiting {poll_interval}s before retry "
                             f"{consecutive_errors}/{max_consecutive_errors}")
                time.sleep(poll_interval)
                elapsed += poll_interval

        logging.error(f"Timeout waiting for job {split_name} after {max_wait_time}s")
        logging.error(f"Check job manually: kubectl get job {split_name} -n {NAMESPACE}")

    except Exception as err:
        logging.error(f"Error in watcher for {split_name}: {err}")
        import traceback
        logging.error(f"Full traceback: {traceback.format_exc()}")
    finally:
        logging.info(f"Watcher thread for {split_name} ending")


def _launch_sorters(uuid_param, experiment, file_path, tpl):
    base_exp = _normalize_experiment_name(experiment)
    cache_uuid = _normalize_uuid_for_cache(uuid_param)
    split_dir = posixpath.join(CACHE_S3_BUCKET, cache_uuid, "original/data")
    legacy_split_dir = posixpath.join(CACHE_S3_BUCKET, cache_uuid, "original/split")

    logging.info(f"Launching sorters for experiment: {base_exp}")
    if cache_uuid != uuid_param:
        logging.info(f"Normalized cache UUID from {uuid_param} to {cache_uuid}")
    logging.info(f"Cache directory: {split_dir}")

    split_files = _list_split_files(split_dir, base_exp)
    if not split_files and legacy_split_dir != split_dir:
        logging.info("No split files in cache data path; checking legacy split path")
        split_files = _list_split_files(legacy_split_dir, base_exp)
        if split_files:
            logging.info(f"Found {len(split_files)} split files in legacy path for {base_exp}")

    if split_files:
        logging.info(f"Found {len(split_files)} split files for {base_exp}")
        _launch_split_sorters(uuid_param, base_exp, split_files, tpl)
    else:
        logging.info("No split files found; launching single sorter job")
        _launch_single_sorter(uuid_param, experiment, file_path, tpl)


def _normalize_uuid_for_cache(uuid_param: str) -> str:
    if not uuid_param:
        return uuid_param
    if uuid_param.startswith("s3://"):
        if uuid_param.startswith(DEFAULT_S3_BUCKET):
            return uuid_param[len(DEFAULT_S3_BUCKET):].strip("/")
        parsed = urlparse(uuid_param)
        key = parsed.path.lstrip("/")
        for prefix in ("ephys/", "integrated/", "fluidics/"):
            if key.startswith(prefix):
                key = key[len(prefix):]
                break
        return key.strip("/")
    return uuid_param.strip("/")


def _list_split_files(split_dir: str, base_exp: str):
    try:
        candidates = wr.list_objects(split_dir)
    except Exception as err:
        logging.warning(f"Could not list split directory {split_dir}: {err}")
        return []

    base_name = posixpath.basename(base_exp)
    prefixes = {f"{base_exp}_well"}
    if base_name != base_exp:
        prefixes.add(f"{base_name}_well")

    out = []
    for path in candidates:
        name = posixpath.basename(path)
        if not (name.endswith(".raw.h5") or name.endswith(".h5")):
            continue
        if any(name.startswith(prefix) for prefix in prefixes):
            match = re.search(r"_well(\d{3})", name)
            if match and int(match.group(1)) < 1:
                continue
            out.append(path)
    return sorted(out)


def _normalize_experiment_name(experiment: str) -> str:
    base = experiment or ""
    while base.endswith(".raw.h5"):
        base = base[:-len(".raw.h5")]
    if base.endswith(".h5"):
        base = base[:-len(".h5")]
    base = re.sub(r"\\.+", ".", base).rstrip(".")
    return base


def _sanitize_job_fragment(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return cleaned or "x"


def _build_well_job_name(uuid_param: str, base_exp: str, well_id: str,
                         prefix: str = JOB_PREFIX, max_len: int = 63) -> str:
    """Per-experiment well sorter Job name. base_exp is the recording stem
    (e.g. "M09875_Rotenone_Baseline_3_05_2026"); well_id is "well001"-"well024"."""
    return format_job_name(f"{base_exp}-{well_id}", prefix=prefix, max_len=max_len)


def _build_splitter_job_name(uuid_param: str, base_exp: str,
                             prefix: str = SPLITTER_JOB_PREFIX, max_len: int = 63) -> str:
    """Per-experiment splitter Job name. The upstream listener used a per-UUID
    name which collided across experiments in the same UUID; we include base_exp
    so each recording gets its own splitter Job."""
    return format_job_name(base_exp, prefix=prefix, max_len=max_len)


def _launch_split_sorters(uuid_param, base_exp, split_files, tpl):
    created = skipped = failed = 0
    failed_wells = []
    for raw_path in split_files:
        well_id = posixpath.basename(raw_path)
        well_id = well_id.replace(".raw.h5", "").replace(".h5", "")
        well_id = well_id.split(f"{base_exp}_", 1)[-1]

        info = tpl.copy()
        info["file_path"] = raw_path
        info["uuid"] = uuid_param
        info["experiment"] = f"{base_exp}_{well_id}"

        job_name = _build_well_job_name(uuid_param, base_exp, well_id, max_len=56)
        logging.info(f"Creating sorter job {job_name} for well {well_id}")
        logging.info(f"Well file path: {raw_path}")

        try:
            kube_job = Kube(job_name, info)
            if not kube_job.check_job_exist():
                result = kube_job.create_job()
                if result == -1:
                    logging.error(f"Failed to create sorter job {job_name}")
                    failed += 1
                    failed_wells.append(well_id)
                else:
                    logging.info(f"Sorter Job {job_name} created successfully")
                    created += 1
                    time.sleep(0.1)
            else:
                logging.info(f"Sorter job {job_name} already exists, skipping")
                skipped += 1
        except Exception as err:
            logging.error(f"Error creating sorter job {job_name}: {err}")
            failed += 1
            failed_wells.append(well_id)

    logging.info(f"Sorter job creation: {created} created, {skipped} skipped, {failed} failed")
    if failed:
        logging.error(f"Failed wells: {', '.join(failed_wells)}")
    if created == 0 and skipped == 0:
        raise Exception("No sorter jobs were created or found - this indicates a serious problem")


def _launch_single_sorter(uuid_param, experiment, file_path, tpl):
    info = tpl.copy()
    info["file_path"] = file_path
    info["uuid"] = uuid_param
    info["experiment"] = experiment.replace(".raw.h5", "").replace(".h5", "")

    job_name = format_job_name(info["experiment"], prefix=JOB_PREFIX)
    logging.info(f"Creating sorter job {job_name} for {file_path}")
    kube_job = Kube(job_name, info)
    if not kube_job.check_job_exist():
        result = kube_job.create_job()
        if result == -1:
            raise Exception(f"Failed to create sorter job {job_name}")
        logging.info(f"Sorter Job {job_name} created successfully")
    else:
        logging.info(f"Sorter job {job_name} already exists, skipping")
