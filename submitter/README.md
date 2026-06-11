# Ephys sorting job submitter

Single-shot CLI for submitting Kilosort2 sorting jobs to the Braingeneers
Kubernetes cluster. Mirrors the orchestration logic of the SpikeCanvas
`Spike_Sorting_Listener` service (MaxOne → one sorter Job; MaxTwo → splitter
Job + per-well sorter fanout) without the MQTT listener, dashboard, or CSV
job-center plumbing.

This directory is self-contained: it can be moved or copied independently of
any other repository. Nothing here imports from `SpikeCanvas-EphysPipeline`.

## Layout

```
submitter/
├── submit.py                       # CLI entry point
├── config/
│   ├── sorting_job_info.json       # sorter image, resources, GPU node whitelist
│   └── splitter_config.json        # MaxTwo splitter image + init container
├── ephys_submit/
│   ├── job_utils.py                # job-name formatter, S3 bucket constants
│   ├── kube.py                     # builds and submits the Kubernetes Job spec
│   ├── splitter_fanout.py          # MaxTwo splitter + per-well fanout watcher
│   └── s3.py                       # boto3 helpers (read metadata.json, list cache)
└── requirements.txt
```

## Setup

```bash
conda run -n spikelab pip install -r requirements.txt
```

You also need:

1. **Kubeconfig** — `~/.kube/config` pointing at the Braingeneers/NRP cluster
   with permission to create Jobs in the `braingeneers` namespace. The
   `prp-s3-credentials` secret must already exist there (the listener relies
   on the same secret).
2. **AWS credentials** — `~/.aws/credentials` with read access to
   `s3://braingeneers/` for reading `metadata.json` and listing split files in
   `s3://braingeneersdev/cache/ephys/`. The endpoint defaults to
   `https://s3.braingeneers.gi.ucsc.edu`; override with the `S3_ENDPOINT_URL`
   env var if needed.

## Usage

```bash
# All experiments listed in the UUID's metadata.json
./submit.py --uuid 2025-05-23-e-MaxTwo_KOLF2.2J_SmitsMidbrain

# A single named experiment
./submit.py --uuid <UUID> --experiment <exp_name>

# A specific S3 file (UUID inferred from path)
./submit.py --file s3://braingeneers/ephys/<UUID>/original/data/<file>.raw.h5

# Override the format detected from metadata.json
./submit.py --file s3://.../foo.raw.h5 --format maxtwo

# Print what would be submitted without contacting Kubernetes
./submit.py --uuid <UUID> --dry-run

# Verbose logging
./submit.py --uuid <UUID> --verbose
```

`./submit.py --help` lists every flag.

## Behavior

For each experiment to submit:

| `data_format` (from metadata.json) | Action |
| --- | --- |
| `maxone` (or anything else) | Submit a single Kilosort2 sorter Job that pulls the file from `s3://braingeneers/ephys/<UUID>/original/data/<file>.raw.h5` |
| `maxtwo` / `max2` | Submit the splitter Job (init container downloads, main container splits per-well to `s3://braingeneersdev/cache/ephys/<UUID>/original/data/`), wait for it to finish, then submit one sorter Job per well file |

For MaxTwo the CLI **blocks** until the splitter Job succeeds and the per-well
sorter Jobs are submitted (the watcher polls every 30 s, with a 2-hour timeout
per splitter). For MaxOne the CLI exits as soon as the single sorter Job is
created.

If a Job with the same name already exists in the cluster, that submission is
skipped; the CLI does not delete or overwrite running jobs. Job names mirror
the listener's scheme (`edp-<uuid-fragment>` for single sorters, `edp-ma2split-<uuid-fragment>`
for splitters, `edp-<uuid-fragment>-well00N` for fanned-out sorters).

If you pass `--file` pointing at an already-split well file (filename matches
`*_wellNNN.raw.h5`), the CLI treats it as a single sorter regardless of the
metadata format — useful for re-sorting one well without re-running the
splitter.

### When `data_format` is missing from metadata.json

Some UUIDs in S3 have a `metadata.json` without a `data_format` field on the
experiment. In that case the CLI logs `[unknown -> single sorter]` and submits
a single sorter Job (matches the upstream listener's behavior). For MaxTwo
recordings whose metadata is missing this field, pass `--format maxtwo`
explicitly to drive the splitter + per-well fanout instead.

## What is *not* mirrored from SpikeCanvas

Deliberately omitted because they are not part of the submission flow itself:

- MQTT listener, message broker setup, Slack notifications
- CSV job center / dashboard pages
- Connectivity, LFP, curation, visualization jobs (the listener also schedules
  these). This tool only submits Kilosort2 sorters and the MaxTwo splitter
  that feeds them. The pipeline image (`braingeneers/ephys_pipeline:v0.79`)
  still runs auto-curation and figure generation internally as part of
  `run.sh`, so curated outputs (`*_acqm.zip`) and figures (`*_figure.zip`)
  still land in S3 — those steps happen inside the sorter container, not in
  separate Jobs.

## Updating image tags

When the cluster moves to a new pipeline tag, edit
`config/sorting_job_info.json` (`image` field) or `config/splitter_config.json`
(`image` field) and re-run `submit.py`. No code changes needed.
