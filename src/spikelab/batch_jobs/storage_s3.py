"""S3-compatible storage helpers for batch job artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    import boto3
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore[assignment]

from ..data_loaders.s3_utils import parse_s3_url
from .models import StoragePathTemplates


class S3StorageClient:
    """Small wrapper around boto3 for upload/download URI handling.

    Path layout is controlled by *path_templates* (a
    :class:`StoragePathTemplates` instance loaded from the active profile).
    """

    def __init__(
        self,
        *,
        prefix: Optional[str] = None,
        path_templates: Optional[StoragePathTemplates] = None,
        endpoint_url: Optional[str] = None,
        region_name: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        aws_session_token: Optional[str] = None,
    ) -> None:
        # ``prefix`` normalisation: ``None`` or empty string stays
        # ``None`` (no bucket-level base configured); a non-empty
        # string gets a trailing ``/`` appended if missing so
        # downstream ``prefix + filename`` concatenation produces
        # a valid S3 URI. Spelt out as three branches instead of a
        # nested ternary for readability. The ``not prefix`` check
        # (rather than ``is None``) is intentional — empty string
        # is a documented synonym for "no prefix".
        if not prefix:
            self.prefix = None
        elif prefix.endswith("/"):
            self.prefix = prefix
        else:
            self.prefix = f"{prefix}/"
        self.endpoint_url = endpoint_url
        self.region_name = region_name
        self._templates = path_templates or StoragePathTemplates()
        # When boto3 is available, eagerly construct the client so
        # tests that patch ``spikelab.batch_jobs.storage_s3.boto3``
        # for the duration of the constructor get the patched client
        # (the original behaviour). When boto3 is None, defer the
        # ImportError until a method that actually needs the client
        # is called — this lets pure-string operations
        # (``build_uri``, ``output_prefix_for_run``,
        # ``logs_prefix_for_run``) succeed on hosts without the
        # optional dependency installed, e.g. ``cli._cmd_render`` →
        # ``_build_session`` → here.
        self._boto3_kwargs = {
            "endpoint_url": endpoint_url,
            "region_name": region_name,
            "aws_access_key_id": aws_access_key_id,
            "aws_secret_access_key": aws_secret_access_key,
            "aws_session_token": aws_session_token,
        }
        if boto3 is not None:
            self._client_instance = boto3.client("s3", **self._boto3_kwargs)
        else:
            self._client_instance = None

    @property
    def _client(self):
        """Return the boto3 S3 client, deferring the ImportError to
        first use when boto3 was not available at construction time.
        """
        if self._client_instance is None:
            if boto3 is None:
                raise ImportError("boto3 is required for S3 storage: pip install boto3")
            self._client_instance = boto3.client("s3", **self._boto3_kwargs)
        return self._client_instance

    def build_uri(self, *, run_id: str, filename: str, category: str = "inputs") -> str:
        """Build an S3 URI for a file using the active path templates.

        ``category`` should be one of the keys defined on
        ``StoragePathTemplates`` (``"inputs"``, ``"outputs"``,
        ``"logs"``). An unknown category silently falls back to the
        ``inputs`` template and emits a ``UserWarning`` so typos
        ("input", "logs/", etc.) don't quietly land in the wrong S3
        prefix.
        """
        if not self.prefix:
            raise ValueError(
                "S3 prefix is not configured. Set it in the profile or command."
            )
        if not hasattr(self._templates, category):
            import warnings

            known = sorted(
                k for k in vars(self._templates).keys() if not k.startswith("_")
            )
            warnings.warn(
                f"build_uri: unknown category={category!r}; falling back "
                f"to the ``inputs`` template. Known categories: {known}. "
                "Check for typos.",
                UserWarning,
                stacklevel=2,
            )
        template = getattr(self._templates, category, self._templates.inputs)
        return template.format(prefix=self.prefix, run_id=run_id, filename=filename)

    def upload_file(self, *, local_path: str, s3_uri: str) -> str:
        """Upload a local file to S3 and return the URI.

        Raises ``FileNotFoundError`` if ``local_path`` does not exist
        rather than deferring to boto3's less informative error.
        """
        if not Path(local_path).is_file():
            raise FileNotFoundError(
                f"upload_file: local_path={local_path!r} does not exist "
                "or is not a regular file."
            )
        bucket, key = parse_s3_url(s3_uri)
        self._client.upload_file(local_path, bucket, key)
        return s3_uri

    def upload_bundle(self, *, local_zip: str, run_id: str) -> str:
        """Upload a zip bundle to S3 under the inputs path template."""
        filename = Path(local_zip).name
        uri = self.build_uri(run_id=run_id, filename=filename, category="inputs")
        return self.upload_file(local_path=local_zip, s3_uri=uri)

    def output_prefix_for_run(self, run_id: str) -> str:
        """Return the S3 prefix for a run's output files."""
        if not self.prefix:
            return ""
        return self._templates.outputs.format(
            prefix=self.prefix, run_id=run_id, filename=""
        )

    def logs_prefix_for_run(self, run_id: str) -> str:
        """Return the S3 prefix for a run's log files."""
        if not self.prefix:
            return ""
        return self._templates.logs.format(
            prefix=self.prefix, run_id=run_id, filename=""
        )

    def download_file(self, *, s3_uri: str, local_path: str) -> str:
        """Download a single file from S3.

        Parameters:
            s3_uri (str): Full ``s3://bucket/key`` URI.
            local_path (str): Destination path on disk.

        Returns:
            local_path (str): The same *local_path* for convenience.
        """
        bucket, key = parse_s3_url(s3_uri)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(bucket, key, local_path)
        return local_path

    def download_output(self, *, run_id: str, filename: str, local_dir: str) -> str:
        """Download a file from the output prefix of a run.

        Parameters:
            run_id (str): Run identifier.
            filename (str): Name of the file within the output prefix.
                ``..`` segments are rejected to prevent path traversal
                outside ``local_dir``.
            local_dir (str): Local directory to save the file into.

        Returns:
            local_path (str): Absolute path of the downloaded file.
        """
        # Path-traversal guard: ``filename`` flows directly into the
        # local filesystem destination. A malicious or buggy upstream
        # (e.g. an S3 listing entry with ``..`` segments) could escape
        # ``local_dir`` and clobber arbitrary files. Resolve both paths
        # and assert the destination stays under the dir.
        local_dir_resolved = Path(local_dir).resolve()
        target = (local_dir_resolved / filename).resolve()
        try:
            target.relative_to(local_dir_resolved)
        except ValueError:
            raise ValueError(
                f"filename={filename!r} resolves outside local_dir={local_dir!r}; "
                "path-traversal segments (e.g. '..') are not allowed."
            )
        prefix = self.output_prefix_for_run(run_id)
        s3_uri = prefix + filename
        return self.download_file(s3_uri=s3_uri, local_path=str(target))

    DEFAULT_LIST_OUTPUT_LIMIT = 10_000

    def list_output_files(self, run_id: str, *, max_keys: Optional[int] = None) -> list:
        """List object keys under the output prefix of a run.

        Parameters:
            run_id (str): Run identifier.
            max_keys (int | None): Cap on the number of keys returned.
                Defaults to ``DEFAULT_LIST_OUTPUT_LIMIT`` (10000) to
                guard against unbounded memory use on long-running jobs
                that produced thousands of intermediate files (QC
                figures, per-recording reports, etc.). Pass an explicit
                larger value if the caller really needs the full list;
                exceeding the cap raises ``ValueError`` rather than
                silently truncating.

        Returns:
            keys (list[str]): S3 object keys found under the output prefix.

        Raises:
            ValueError: When more than ``max_keys`` objects exist under
                the prefix.
        """
        prefix = self.output_prefix_for_run(run_id)
        if not prefix:
            return []
        cap = self.DEFAULT_LIST_OUTPUT_LIMIT if max_keys is None else max_keys
        bucket, key_prefix = parse_s3_url(prefix)
        paginator = self._client.get_paginator("list_objects_v2")
        keys: list = []
        for page in paginator.paginate(Bucket=bucket, Prefix=key_prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
                if len(keys) > cap:
                    raise ValueError(
                        f"list_output_files: more than max_keys={cap} objects "
                        f"under prefix={prefix!r}. Pass a larger ``max_keys`` "
                        "if this is expected; otherwise narrow the run_id."
                    )
        return keys
