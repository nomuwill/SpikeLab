# Culture Lifecycle & Burst Analysis Reference

Reference for the orchestrator: how to interpret a well's electrophysiological state, compile a burst report, and judge its lifecycle stage. This document holds the domain knowledge — analysis parameters, reference ranges, stage characterizations, transition rules — so the orchestrator's operational playbook ([SKILL.md](../.claude/skills/orchestrator/SKILL.md)) stays short and the science stays in one place.

**Core principle:** reference ranges are **guidelines for holistic judgment**, not hard gates. A well close on most metrics with positive trends is good_age even if one metric is slightly outside range. The orchestrator's job is LLM-style reasoning over the full report + history, not threshold cascades.

---

## Tiered analysis

The orchestrator escalates what it asks `spikelab` to compute based on each well's developmental state. This avoids wasting compute on cultures too young to produce meaningful higher-order metrics.

### Tier 1 — Unit stats (always runs)

Request from `spikelab`:
- Total sorted units
- Per-unit firing rates
- Count of **active units** (firing rate > 0.2 Hz)

**Escalation gate:** `active_units ≥ 30` → Tier 2. Otherwise stop at Tier 1 for this cycle.

Edge case: a drop from ≥30 back below 30 on a well previously at good_age is a senescence signal, not a reason to skip analysis — continue Tier 1 but flag for lifecycle re-evaluation (see too_old criteria).

### Tier 2 — Population rate

Request from `spikelab`:
- Population rate (binned spike count across all units)
- Population rate RMS

Let `spikelab` own bin size, smoothing defaults — only specify parameters you care about.

**Escalation gate:** RMS indicates meaningful fluctuation (not flat noise) → Tier 3.

### Tier 3 — Burst analysis

Request from `spikelab` with **explicit parameters** where the orchestrator cares:
- Burst sensitivity sweep: 1–5× RMS, 0.25 step
- Minimum inter-burst interval: 2000 ms
- Edge threshold: 0.2
- Per-burst unit participation
- Hyperactivity/silence episode detection (episodes > 10 s)

Returned: burst metrics at each sweep threshold (count, durations, IBIs), per-burst participation stats, detected hyperactivity/silence episodes.

---

## Burst report

Compile once per well per cycle from the analysis results + history from `culture_log.json`. This is the **sole input** to lifecycle evaluation — reason over the whole report, don't thresh-check each field.

### Unit activity
- Total sorted units
- Active units (FR > 0.2 Hz)
- Mean, median firing rate across active units

### Population dynamics
- Population rate RMS
- Hyperactivity episodes (>10 s continuous high activity), durations
- Silence episodes (>10 s very low activity), durations

### Burst profile (from Tier 3 sensitivity sweep)
- Burst count at each threshold in the sweep
- Mean burst duration and duration variability per threshold
- **At the optimal threshold:**
  - Burst rate per minute
  - Mean/median burst duration
  - Mean/median inter-burst interval (IBI)
  - IBI coefficient of variation (CV)
  - Per-burst unit participation: fraction of active units contributing ≥ 2 spikes per burst
  - All-burst units: count of units present in every detected burst

### History context
- Days since plating / experiment start
- Current lifecycle stage, days at current stage
- Previous stage transitions + dates
- Experiments completed on this well

### Trends over last N cycles (default N = 5)
- Firing rate trend (increasing / stable / declining)
- Burst rate trend
- Participation trend
- Duration trend
- IBI regularity trend

### Reference ranges (guidelines, not gates)
| Metric | Healthy good_age range |
|---|---|
| Burst rate | ~5–15 / min |
| Burst duration | ~500–2000 ms |
| IBI regularity (CV) | < ~0.5 |
| Unit participation per burst | ≥ ~50% of active units (≥ 2 spikes each) |
| All-burst units | at least some units present in every burst |

---

## Lifecycle stages

Three stages, one-way transitions.

### too_young

Characterized by any of:
- Fewer than 30 active units (FR > 0.2 Hz), **or**
- No clearly defined population bursting, **or**
- Very irregular / occasional bursting: burst peaks below 2× population rate RMS, durations outside ~300 ms+, fewer than 4–5 good bursts/min **or** more than 30 bursts/min.

**Action:** continue recording and analysis. Escalate analysis tier as activity develops. Perform media changes on schedule. No experiments.

### good_age

Characterized by all of:
- Sufficient active units (≥ 30)
- Clearly defined population bursting with peaks above 2× RMS (ideally larger — burst sensitivity sweep will reveal the shape)
- Burst durations around 500–2000 ms
- Regular inter-burst intervals
- ≥ ~50% unit participation per burst (≥ 2 spikes each)
- Some units participating in all bursts with ≥ 2 spikes

**Stability requirement:** ≥ 3 consecutive cycles of consistent metrics before triggering experiments. Positive trends reinforce the call; negative trends on borderline metrics warrant holding at too_young another cycle.

**Action:** run pending experiment(s) per the well's plan. Continue monitoring for transition to too_old.

### too_old

Characterized by **either**:
- Sustained hyperactivity/silence pattern: long (>10 s) hyperactivity followed by long (>10 s) near-silence, repeated across cycles, **or**
- Drop in active units below 30 after previously being at good_age.

**Critical distinction:** the hyperactivity/silence pattern can also occur transiently in young cultures. **A well can only be classified as too_old if it was previously good_age, OR the pattern is sustained across 5+ consecutive cycles.** Without prior good_age status, low unit counts mean too_young, not too_old.

**Action:** continue recording to capture the full lifecycle. No experiments triggered. too_old is terminal — no recovery path.

---

## Transition rules

Valid: `too_young → good_age → too_old`.
- Never skip `too_young → too_old`.
- No recovery from too_old to any other stage.
- First-time `good_age` transition: store the current burst report as `baseline.json` for this well (reference for experiment comparison).

If your judgment suggests an invalid transition (e.g. sudden "too_old" without history), this is a strong signal to check for a confound: recording artifact, bad electrode selection, sorter failure, or a transient developmental phase that looks like senescence. **Escalate to the user** rather than force the classification.

---

## Logging your reasoning

Every lifecycle evaluation must write to `culture_log.json`:

```json
{
  "timestamp": "2026-04-13T09:15:00-07:00",
  "event_type": "lifecycle_evaluation",
  "well_id": 2,
  "stage_assigned": "good_age",
  "prior_stage": "too_young",
  "transition": true,
  "burst_report_ref": "orchestrator/<plan_id>/well_2/latest_report.json",
  "reasoning": "Active units 47 (above gate). Burst profile: rate 8/min, duration mean 780 ms, IBI CV 0.32, participation 62%. All 6 metrics in healthy range. Trends over last 5 cycles: firing rate increasing, participation rising, IBI regularity improving. 3 consecutive cycles within good_age ranges. First-time transition — storing baseline.",
  "triggered_experiment": true
}
```

Reasoning is both audit trail and your own context for future cycles. Treat it as a letter to the next orchestrator session.

---

## When to escalate to the user (science, not commands)

- Unusual artifact patterns in a recording flagged by a subagent.
- Anomalous burst profile vs. the last 5 cycles with no obvious cause.
- Ambiguous lifecycle transitions — e.g. hyperactivity/silence in a well that was never good_age (developmental transient, or early senescence despite no prior good_age?).
- Multiple subagent retries failed analysis for the same well.
- Metrics on the edge of reference ranges across multiple cycles without a clear direction.

**Do not escalate** for routine uncertainty in tier gates or minor reference-range deviations — make the call, log the reasoning, keep going. The whole point is that you own the scientific judgment.
