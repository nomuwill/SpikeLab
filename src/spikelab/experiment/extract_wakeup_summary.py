"""
extract_wakeup_summary.py — read a `claude -p --output-format stream-json`
transcript (JSONL, one event per line) from stdin and emit a compact
human-readable summary to stdout.

Used in `wake_up.sh` to produce the short-form `<role>_<ts>.log` alongside the
full `<role>_<ts>.jsonl` transcript. The jsonl is the source of truth for
post-mortems; the .log is the elevator summary.

Output includes: session_id, model, duration, turn count, cost, tool-call
counts by name, and the final assistant text. On early termination (no result
event) the extractor falls back to the last assistant text turn and flags it
as "transcript truncated".

stdlib only — runs with any python3 including the MaxLab bundled interpreter.

Usage:
    claude -p --output-format stream-json ... | \\
        tee <log>.jsonl | \\
        python extract_wakeup_summary.py >> <log>
"""

import json
import sys
from collections import Counter


def main():
    result_event = None
    init_event = None
    last_assistant_text = None
    tool_uses = Counter()
    tool_errors = 0
    n_events = 0
    n_parse_errors = 0

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            n_parse_errors += 1
            continue
        n_events += 1
        t = ev.get("type")

        if t == "system" and ev.get("subtype") == "init":
            init_event = ev
        elif t == "result":
            result_event = ev
        elif t == "assistant":
            msg = ev.get("message") or {}
            for block in msg.get("content") or []:
                btype = block.get("type")
                if btype == "text":
                    txt = block.get("text")
                    if txt:
                        last_assistant_text = txt
                elif btype == "tool_use":
                    name = block.get("name") or "<unknown>"
                    tool_uses[name] += 1
        elif t == "user":
            # user turn may carry tool_result blocks for prior tool_use calls
            msg = ev.get("message") or {}
            for block in msg.get("content") or []:
                if block.get("type") == "tool_result" and block.get("is_error"):
                    tool_errors += 1

    # Emit summary.
    out = []
    if init_event:
        out.append(f"session_id: {init_event.get('session_id')}")
        out.append(f"model: {init_event.get('model')}")
        out.append(f"cwd: {init_event.get('cwd')}")

    if result_event:
        dur_ms = result_event.get("duration_ms") or 0
        cost = result_event.get("total_cost_usd")
        out.append(
            f"result: {result_event.get('subtype')} "
            f"(is_error={result_event.get('is_error')}) "
            f"num_turns={result_event.get('num_turns')} "
            f"duration={dur_ms / 1000:.1f}s"
            + (f" cost=${cost:.4f}" if isinstance(cost, (int, float)) else "")
        )
    else:
        out.append("result: TRANSCRIPT TRUNCATED — no result event observed")

    if tool_uses:
        top = ", ".join(f"{n}={c}" for n, c in tool_uses.most_common())
        out.append(f"tool_calls: {top}")
    else:
        out.append("tool_calls: none")

    if tool_errors:
        out.append(f"tool_errors: {tool_errors}")

    out.append(f"events_parsed: {n_events} (json_errors={n_parse_errors})")

    out.append("---")
    if result_event and "result" in result_event:
        out.append("final:")
        out.append(str(result_event["result"]).strip())
    elif last_assistant_text:
        out.append("final (from last assistant turn — no result event):")
        out.append(last_assistant_text.strip())
    else:
        out.append("final: <no assistant text emitted>")

    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
