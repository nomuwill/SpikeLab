"""
Shared constants and helpers for the ephys job submitter.
Vendored from SpikeCanvas-EphysPipeline/Services/Spike_Sorting_Listener/src/job_utils.py
"""
import re

JOB_PREFIX = "edp-"
DEFAULT_S3_BUCKET = "s3://braingeneers/ephys/"
CACHE_S3_BUCKET = "s3://braingeneersdev/cache/ephys/"
NAMESPACE = "braingeneers"


def format_job_name(raw_name: str,
                    job_ind: int | None = None,
                    prefix: str = JOB_PREFIX,
                    max_len: int = 63) -> str:
    """
    Format a raw name into a Kubernetes-safe job name.
    """
    stem = raw_name
    if raw_name.endswith(".csv") and job_ind is not None:
        stem = f"{raw_name[:-4]}-{job_ind}"
    elif raw_name.endswith(".raw.h5"):
        stem = raw_name[:-8]
    elif raw_name.endswith(".h5"):
        stem = raw_name[:-3]

    stem = re.sub(r"[^a-z0-9]+", "-", stem.lower())
    stem = stem.strip("-")

    full = f"{prefix}{stem}"
    if len(full) > max_len:
        keep = max_len - len(prefix)
        full = f"{prefix}{stem[-keep:]}"
        full = full.lstrip("-") or "x"

    return full
