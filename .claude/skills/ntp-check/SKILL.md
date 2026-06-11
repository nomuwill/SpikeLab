---
name: ntp-check
description: Verify clock synchronization between MaxTwo (local workstation) and the habitat Pi for cross-machine event alignment in experiment manifests. Use at orchestrator session start, after any reboot or NTP-config change on either host, or when manifest event frame_offsets look suspicious (negative, or beyond recording length).
---

# NTP Verification (MaxTwo ↔ Pi)

Produces a cross-host clock-offset bound so that manifest `events[].timestamp_ms` (e.g. a `habitat_operation` cross-reference) can be joined to recording `start_time_ms` for `frame_offset` computation.

**Target:** cross-host offset ≤ **100 ms** (precision needed for baseline/drug event splits in ephys recordings). For sub-ms alignment (closed-loop stim, shared-clock experiments), NTP is **not** sufficient — require a hardware trigger or shared clock signal.

---

## Why indirect, not round-trip?

**Do NOT attempt to measure the offset directly by timing a round-trip through the MCP channel.** The MCP path has multi-second latency and Claude Code's parallel-tool scheduling does not fire bash + MCP calls simultaneously enough to bracket send/receive events. Any direct measurement will produce noise many seconds wide and is confidently misleading.

Instead: compute an **upper bound on cross-host offset** as the sum of each host's independent error relative to UTC, reported by its own NTP subsystem. Both hosts sync against the public internet NTP pool, so their errors are independent and both on the order of milliseconds.

---

## Procedure

### 1. MaxTwo side

```bash
timedatectl status
timedatectl show-timesync 2>&1 | head -20
```

Required from `timedatectl status`:
- `System clock synchronized: yes`
- `NTP service: active`

Compute upper bound on MaxTwo's offset to UTC from `show-timesync` → `NTPMessage` line:

```
maxtwo_offset_upper_ms = RootDispersion_ms + RootDelay_ms / 2
```

Typical healthy values: stratum ≤ 3, RootDelay < 50 ms, RootDispersion < 10 ms, Jitter < 10 ms.

### 2. Pi (habitat) side

```
check_system_ready()     # via habitat-remote MCP
```

Inspect the `ntp` block in the response, for example:

```json
{"name":"ntp","status":"pass","detail":{"leap_status":"Normal","offset_ms":0.22,"stratum":3,"issues":[]}}
```

Required:
- `status: "pass"`
- `leap_status: "Normal"`
- `issues: []`
- `offset_ms` — record this as `pi_offset_ms`

### 3. Cross-host bound

```
cross_host_offset_bound_ms = maxtwo_offset_upper_ms + abs(pi_offset_ms)
```

---

## Decision matrix

| Condition | Action |
|---|---|
| Both sides pass, bound < 10 ms | **Proceed.** Log the bound. Baseline/drug splits and all experiment cross-refs are safe. |
| Bound 10–100 ms | **Proceed with flag.** Usable for event cross-refs at ~100 ms resolution; note the reduced headroom in the caller's log. Not usable for sub-10ms work. |
| Bound > 100 ms | **Stop.** Escalate to user. Do not run experiments — event cross-refs will be unreliable and the ~100 ms target is missed. |
| Either side reports `unsynchronised` / `leap_status != Normal` / `issues` non-empty | **Stop.** Escalate. Do not attempt to infer a bound from an unsynced clock. |
| NTP service inactive on MaxTwo (`timedatectl status` → "NTP service: inactive") | **Stop.** Escalate. User needs to start the time sync service. |

---

## When to run

- **Mandatory** at orchestrator session start.
- After a reboot of MaxTwo or Pi.
- After any NTP configuration change on either host (`/etc/systemd/timesyncd.conf`, chrony config, NTP source change, timezone change).
- If a recording manifest produces an impossible `frame_offset`: negative, or larger than the recording's frame count. This strongly suggests clock drift between the manifest writer and the event source.

---

## Reference verification (2026-04-13)

Baseline measurement on this setup:
- MaxTwo: stratum 2, syncs to `ntp.ubuntu.com`. RootDelay 5.5 ms, RootDispersion 0.55 ms, Jitter 2.4 ms. `maxtwo_offset_upper_ms ≈ 3.3`.
- Pi: stratum 3, chrony `offset_ms = 0.22`, leap Normal, NTP active.
- Bound: **≈ 3.5 ms** — well within the 10 ms band.

Use these values as a sanity check if a fresh measurement looks anomalous. A large unexplained departure (e.g. Pi `offset_ms` > 100) likely indicates the Pi's NTP source is broken, not that the real offset changed dramatically.

---

## What this skill does NOT do

- Does not measure cross-host offset via round-trip timing. See "Why indirect" above.
- Does not fix NTP problems. If a check fails, escalate to the user with the specific failing field — do not try to restart time services or edit configs.
- Does not address alignment with instruments other than MaxTwo and Pi. If cameras, stimulators, or behavioural rigs join the stack, document each one's clock source and compute pairwise bounds the same way.
- Does not guarantee hard real-time alignment. NTP's ~ms precision is not enough for closed-loop stim or any sub-ms experiment — those need hardware triggers.
