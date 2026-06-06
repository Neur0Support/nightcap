#!/usr/bin/env python3
# =============================================================================
#  codex-snapshot.py  --  Codex usage adapter for Nightcap
#
#  >>> UNTESTED AGAINST A REAL ~/.codex / LIVE CODEX ACCOUNT <<<
#
#  The parsing is built to the documented Codex session schema and verified
#  against synthetic events, but it has NOT been run on a real Codex install.
#  Field names (payload.rate_limits.primary/secondary.used_percent,
#  window_minutes, resets_in_seconds, top-level ISO `timestamp`) come from
#  xiangz19/codex-ratelimit and the ccusage Codex reader. If a Codex user
#  confirms or corrects the schema, update this file and drop the warning.
#  Nightcap fails closed, so a wrong guess makes the overnight run STAND DOWN,
#  it does not misfire.
# =============================================================================
"""Read OpenAI Codex's local session logs and write the snapshot nightcap reads.

Run it right before nightcap in your overnight job:

    python3 adapters/codex-snapshot.py && python3 nightcap.py

It writes the same shape statusline.js writes for Claude Code:

    { "captured_at": "<ISO8601 of the Codex event>",
      "five_hour": { "used_percentage": <int|null>, "resets_at": <epoch|null> },
      "seven_day": { "used_percentage": <int|null>, "resets_at": <epoch|null> } }

captured_at is the Codex event's OWN timestamp, not now(), so Nightcap's staleness
gate still means "how long since you were last active", exactly as on Claude.
Env: CODEX_HOME (default ~/.codex), NIGHTCAP_SNAPSHOT (default ~/.claude/usage-snapshot.json).
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

CODEX_HOME = Path(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")))
SESSIONS   = CODEX_HOME / "sessions"
TARGET     = os.environ.get("NIGHTCAP_SNAPSHOT", os.path.expanduser("~/.claude/usage-snapshot.json"))
MAX_FILES  = int(os.environ.get("CODEX_MAX_FILES", 40))   # newest N rollout files to scan


def fail(why):
    # Do NOT write a snapshot on failure. Nightcap then reads the previous one (or
    # none) and fails closed on staleness, which is the safe outcome.
    print(f"codex-snapshot: {why}", file=sys.stderr)
    sys.exit(1)


def find_latest_ratelimit():
    """Return (record, rate_limits) for the most recent event carrying rate_limits."""
    if not SESSIONS.is_dir():
        fail(f"no sessions dir at {SESSIONS}")
    files = sorted(SESSIONS.rglob("rollout-*.jsonl"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        fail(f"no rollout-*.jsonl under {SESSIONS}")
    for path in files[:MAX_FILES]:
        found = None
        try:
            with open(path, encoding="utf-8-sig") as f:
                for line in f:
                    if "rate_limits" not in line:      # cheap filter before json.loads
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    rl = (rec.get("payload") or {}).get("rate_limits") or rec.get("rate_limits")
                    if rl:
                        found = (rec, rl)              # keep the LAST one in the file
        except OSError:
            continue
        if found:
            return found
    fail(f"session files found but no rate_limits in the newest {min(MAX_FILES, len(files))}")


def main():
    rec, rl = find_latest_ratelimit()

    # Codex names the windows primary/secondary; pick by length so we don't depend on
    # order. The longer window_minutes is the weekly, the shorter is the 5-hour.
    windows = [w for w in rl.values() if isinstance(w, dict) and "used_percent" in w]
    if not windows:
        fail("rate_limits present but no window carried used_percent")
    by_len = sorted(windows, key=lambda w: w.get("window_minutes", 0))
    short, long = by_len[0], by_len[-1]

    ts = rec.get("timestamp")
    if not ts:
        fail("event has no timestamp; cannot date the reading")
    try:
        base = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        fail(f"unparseable timestamp: {ts!r}")

    def pct(w):
        v = w.get("used_percent")
        return round(v) if isinstance(v, (int, float)) else None

    def resets_at(w):
        s = w.get("resets_in_seconds")
        return int(base + s) if isinstance(s, (int, float)) else None

    snap = {
        "captured_at": ts,
        "five_hour": {"used_percentage": pct(short), "resets_at": resets_at(short)},
        "seven_day": {"used_percentage": pct(long),  "resets_at": resets_at(long)},
    }
    os.makedirs(os.path.dirname(TARGET) or ".", exist_ok=True)
    with open(TARGET, "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2)
    print(f"codex-snapshot: wrote {TARGET} "
          f"(5h {snap['five_hour']['used_percentage']}%, "
          f"wk {snap['seven_day']['used_percentage']}%, captured {ts})")


if __name__ == "__main__":
    main()
