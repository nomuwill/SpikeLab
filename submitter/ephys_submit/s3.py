"""
Tiny boto3 wrapper used by the submitter.

Replaces the listener's `braingeneers.utils.s3wrangler` dependency with the
two operations we actually need: list objects under an s3:// prefix and read
a JSON file. Endpoint defaults to the braingeneers ceph endpoint and can be
overridden via the S3_ENDPOINT_URL environment variable.
"""
import json
import os
from urllib.parse import urlparse

import boto3

DEFAULT_ENDPOINT = os.environ.get("S3_ENDPOINT_URL", "https://s3.braingeneers.gi.ucsc.edu")


def _client():
    return boto3.client("s3", endpoint_url=DEFAULT_ENDPOINT)


def _parse(uri: str):
    parsed = urlparse(uri)
    if parsed.scheme != "s3":
        raise ValueError(f"Not an s3:// URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def list_objects(prefix_uri: str):
    """List object URIs (s3://bucket/key) under the given prefix."""
    bucket, prefix = _parse(prefix_uri.rstrip("/") + "/")
    s3 = _client()
    paginator = s3.get_paginator("list_objects_v2")
    out = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents") or []:
            out.append(f"s3://{bucket}/{obj['Key']}")
    return out


def read_json(uri: str):
    bucket, key = _parse(uri)
    obj = _client().get_object(Bucket=bucket, Key=key)
    return json.loads(obj["Body"].read())
