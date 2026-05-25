"""Load cluster profile presets for job execution."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any, Dict

import yaml

from .models import ClusterProfile


def _read_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid profile file: {path}")
    return data


def load_cluster_profile(path: str) -> ClusterProfile:
    """Load a profile from an explicit YAML path."""
    payload = _read_yaml(Path(path))
    return ClusterProfile.model_validate(payload)


#: Map from accepted profile-name aliases to the YAML file shipped
#: under ``spikelab.batch_jobs.profiles``. Add new built-in profiles
#: here so ``load_profile_from_name`` recognises them explicitly
#: instead of silently falling back to ``defaults``.
_KNOWN_PROFILE_NAMES: Dict[str, str] = {
    "nrp": "nrp.yaml",
    "nautilus": "nrp.yaml",
    "defaults": "defaults.yaml",
    "default": "defaults.yaml",
}


def load_profile_from_name(name: str) -> ClusterProfile:
    """Load one of the built-in profile files by name.

    Unknown names fall back to ``defaults.yaml`` for backward
    compatibility, but emit a ``UserWarning`` listing the recognised
    profile aliases so a typo doesn't silently land on the wrong
    profile.
    """
    import warnings

    normalized = name.strip().lower()
    filename = _KNOWN_PROFILE_NAMES.get(normalized)
    if filename is None:
        warnings.warn(
            f"Unknown profile name {name!r}; falling back to "
            f"``defaults.yaml``. Recognised aliases: "
            f"{sorted(_KNOWN_PROFILE_NAMES.keys())}. Pass "
            "``--profile-file <path>`` to load an explicit YAML.",
            UserWarning,
            stacklevel=2,
        )
        filename = "defaults.yaml"
    base = files("spikelab.batch_jobs").joinpath("profiles")
    payload = _read_yaml(Path(str(base.joinpath(filename))))
    return ClusterProfile.model_validate(payload)
