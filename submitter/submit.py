#!/usr/bin/env python3
"""
Single-shot Kilosort2 sorting job submitter.

Mirrors the behavior of the SpikeCanvas Spike_Sorting_Listener (MaxOne -> single
sorter Job; MaxTwo -> splitter Job + per-well sorter fanout) but invoked manually
from the CLI instead of from MQTT.

Examples
--------
# All experiments listed in the UUID's metadata.json (auto-detect maxone/maxtwo)
./submit.py --uuid 2025-05-23-e-MaxTwo_KOLF2.2J_SmitsMidbrain

# A single experiment by name
./submit.py --uuid <UUID> --experiment <exp_name>

# A specific S3 file (UUID inferred from the path)
./submit.py --file s3://braingeneers/ephys/<UUID>/original/data/<file>.raw.h5

# Override the format detected from metadata.json
./submit.py --file s3://.../<file>.raw.h5 --format maxtwo

# Show what would be submitted without contacting Kubernetes
./submit.py --uuid <UUID> --dry-run

For MaxTwo datasets the process blocks until the splitter Job succeeds and the
per-well sorter Jobs are submitted (the watcher thread is non-daemon). For
MaxOne datasets the process exits as soon as the single sorter Job is created.
"""
import argparse
import json
import logging
import posixpath
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

REPO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_DIR))

from ephys_submit import s3 as s3util  # noqa: E402
from ephys_submit.job_utils import DEFAULT_S3_BUCKET  # noqa: E402
from ephys_submit.splitter_fanout import (  # noqa: E402
    _launch_single_sorter,
    spawn_splitter_fanout,
)

CONFIG_DIR = REPO_DIR / "config"


def load_sorter_template() -> dict:
    return json.loads((CONFIG_DIR / "sorting_job_info.json").read_text())


def load_splitter_config() -> dict:
    return json.loads((CONFIG_DIR / "splitter_config.json").read_text())


def is_maxtwo(fmt: str | None) -> bool:
    return bool(fmt) and str(fmt).lower() in {"maxtwo", "max2"}


def is_split_well_file(file_path: str) -> bool:
    """An already-split MaxTwo well file should be sorted as a single sorter."""
    return bool(re.search(r"_well\d{3}", posixpath.basename(file_path)))


def derive_uuid_from_s3(uri: str) -> str | None:
    parsed = urlparse(uri)
    parts = parsed.path.lstrip("/").split("/")
    if len(parts) >= 2 and parts[0] == "ephys":
        return parts[1]
    return None


def _strip_h5(name: str) -> str:
    if name.endswith(".raw.h5"):
        return name[: -len(".raw.h5")]
    if name.endswith(".h5"):
        return name[: -len(".h5")]
    return name


def find_experiments(metadata: dict, *, exp_name: str | None = None,
                     file_path: str | None = None):
    """Yield (name, block_path, fmt) tuples that match the filters."""
    experiments = metadata.get("ephys_experiments") or {}
    target_base = None
    if file_path:
        base = posixpath.basename(file_path)
        base = re.sub(r"_well\d{3}", "", base)
        target_base = _strip_h5(base)

    for name, exp in experiments.items():
        blocks = exp.get("blocks") or []
        if not blocks:
            continue
        block_path = blocks[0].get("path", "")
        fmt = (exp.get("data_format") or "").lower()
        if exp_name and name != exp_name:
            continue
        if target_base is not None:
            block_base = _strip_h5(posixpath.basename(block_path))
            if block_base != target_base:
                continue
        yield name, block_path, fmt


def submit_one(uuid: str, exp_name: str, file_path: str, fmt: str | None,
               *, sorter_tpl: dict, splitter_cfg: dict) -> None:
    if is_maxtwo(fmt) and not is_split_well_file(file_path):
        logging.info(f"[{exp_name}] format=maxtwo -> splitter + per-well fanout")
        spawn_splitter_fanout(uuid, exp_name, file_path, splitter_cfg, sorter_tpl)
    else:
        if is_maxtwo(fmt) and is_split_well_file(file_path):
            logging.info(f"[{exp_name}] format=maxtwo but file is already split -> single sorter")
        else:
            logging.info(f"[{exp_name}] format={fmt or 'maxone'} -> single sorter")
        _launch_single_sorter(uuid, exp_name, file_path, sorter_tpl)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--uuid", help="Experiment UUID, e.g. 2025-05-23-e-MaxTwo_KOLF2.2J_SmitsMidbrain")
    parser.add_argument("--file", help="Specific S3 path to a .raw.h5 file (UUID inferred if omitted)")
    parser.add_argument("--experiment", help="Submit only this experiment from metadata.json")
    parser.add_argument("--format", choices=["maxone", "maxtwo"],
                        help="Override data_format from metadata.json")
    parser.add_argument("--s3-base", default=DEFAULT_S3_BUCKET,
                        help="S3 base for braingeneers/ephys (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be submitted; do not contact Kubernetes or S3 listing")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not args.uuid and not args.file:
        parser.error("Provide --uuid and/or --file")

    uuid = args.uuid or derive_uuid_from_s3(args.file)
    if not uuid:
        parser.error("Could not derive UUID from --file path; pass --uuid explicitly")

    metadata_uri = f"{args.s3_base.rstrip('/')}/{uuid}/metadata.json"
    metadata: dict | None
    try:
        metadata = s3util.read_json(metadata_uri)
        logging.info(f"Loaded metadata.json for {uuid}")
    except Exception as err:
        if args.file and args.format:
            logging.warning(f"Could not read {metadata_uri} ({err}); proceeding with --file/--format")
            metadata = None
        else:
            parser.error(
                f"Could not read {metadata_uri}: {err}\n"
                f"Pass --file plus --format to bypass metadata.json discovery."
            )

    sorter_tpl = load_sorter_template()
    splitter_cfg = load_splitter_config()

    targets: list[tuple[str, str, str | None]] = []  # (exp_name, file_path, fmt)
    if metadata is not None and not args.file:
        for name, block, fmt in find_experiments(metadata, exp_name=args.experiment):
            full_path = posixpath.join(args.s3_base.rstrip("/"), uuid, block)
            targets.append((name, full_path, args.format or fmt))
        if args.experiment and not targets:
            parser.error(f"Experiment '{args.experiment}' not found in metadata.json")
    elif metadata is not None and args.file:
        matches = list(find_experiments(metadata, file_path=args.file))
        if not matches:
            logging.warning(f"No experiment in metadata.json matched --file {args.file}; "
                            f"using filename as experiment name")
            name = _strip_h5(posixpath.basename(args.file))
            targets.append((name, args.file, args.format))
        else:
            for name, _block, fmt in matches:
                targets.append((name, args.file, args.format or fmt))
    else:
        # metadata absent, --file + --format provided
        name = _strip_h5(posixpath.basename(args.file))
        targets.append((name, args.file, args.format))

    if not targets:
        parser.error("No experiments to submit")

    logging.info(f"Will submit {len(targets)} experiment(s) under UUID {uuid}:")
    for name, path, fmt in targets:
        kind = "maxtwo splitter+fanout" if (is_maxtwo(fmt) and not is_split_well_file(path)) else "single sorter"
        logging.info(f"  - {name}  [{fmt or 'unknown'} -> {kind}]  {path}")

    if args.dry_run:
        logging.info("Dry-run: not contacting Kubernetes. Exiting.")
        return 0

    failures = 0
    for name, path, fmt in targets:
        try:
            submit_one(uuid, name, path, fmt,
                       sorter_tpl=sorter_tpl, splitter_cfg=splitter_cfg)
        except Exception as err:
            logging.error(f"Failed to submit {name}: {err}")
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
