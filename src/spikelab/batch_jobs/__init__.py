"""Kubernetes batch-job launching helpers for SpikeLab.

Requires the ``batch-jobs`` optional dependency group::

    pip install spikelab[batch-jobs]
"""


def __getattr__(name):
    """Lazy-import public symbols so the package is importable without extras."""
    _public = {
        "ClusterProfile",
        "ContainerSpec",
        "JobSpec",
        "ResourceSpec",
        "RunConfig",
        "SubmitResult",
        "VolumeMountSpec",
        "RunSession",
        "load_cluster_profile",
        "load_profile_from_name",
    }
    if name in _public:
        try:
            if name in {
                "ClusterProfile",
                "ContainerSpec",
                "JobSpec",
                "ResourceSpec",
                "RunConfig",
                "SubmitResult",
                "VolumeMountSpec",
            }:
                from .models import (
                    ClusterProfile,
                    ContainerSpec,
                    JobSpec,
                    ResourceSpec,
                    RunConfig,
                    SubmitResult,
                    VolumeMountSpec,
                )

                return locals()[name]
            if name in {"load_cluster_profile", "load_profile_from_name"}:
                from .profiles import load_cluster_profile, load_profile_from_name

                return locals()[name]
            if name == "RunSession":
                from .session import RunSession

                return RunSession
        except ImportError as exc:
            # Tier L-D9: only emit the install-hint message when the
            # missing module's name is one of the known optional
            # dependencies. An ImportError raised by a typo inside
            # our own module would otherwise be hidden behind the
            # generic "install the batch-jobs extra" message, sending
            # the operator down the wrong troubleshooting path.
            _OPTIONAL_DEPS = {
                "kubernetes",
                "boto3",
                "yaml",
                "jinja2",
                "pydantic",
            }
            missing_name = getattr(exc, "name", None)
            if missing_name is None or missing_name.split(".")[0] in _OPTIONAL_DEPS:
                raise ImportError(
                    f"Cannot import '{name}' — install the batch-jobs extra: "
                    "pip install spikelab[batch-jobs] "
                    f"(underlying ImportError: {exc})"
                ) from exc
            # Re-raise unchanged for non-optional-dep errors (typos in
            # our relative imports, etc.) so the real cause surfaces.
            raise
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ClusterProfile",
    "ContainerSpec",
    "JobSpec",
    "ResourceSpec",
    "RunConfig",
    "SubmitResult",
    "VolumeMountSpec",
    "RunSession",
    "load_cluster_profile",
    "load_profile_from_name",
]
