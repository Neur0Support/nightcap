#!/usr/bin/env python3
"""nightcap.py -- decide how hard your overnight agent runs, from your remaining Claude usage.

Reads the snapshot your status line writes (statusline.js). Prints a plan, or skips.
Fails closed when the reading can't be trusted. Not a cap. A nightcap.  https://neurosupport.co.nz

Output: "run: <band> band, <model> model"  or  "skip: <why>"
Thresholds are env vars: NIGHTCAP_SNAPSHOT, NIGHTCAP_STALE_MIN, NIGHTCAP_STALE_MAX_H,
NIGHTCAP_CEILING, NIGHTCAP_SURPLUS_BELOW, NIGHTCAP_THROTTLE_ABOVE.
"""
import json
import os
import sys
from datetime import datetime, timezone

SNAPSHOT       = os.environ.get("NIGHTCAP_SNAPSHOT", os.path.expanduser("~/.claude/usage-snapshot.json"))
STALE_MIN      = int(os.environ.get("NIGHTCAP_STALE_MIN", 30))      # fresher -> you look active -> skip
STALE_MAX_H    = int(os.environ.get("NIGHTCAP_STALE_MAX_H", 12))    # older   -> can't trust it   -> fail closed
CEILING        = int(os.environ.get("NIGHTCAP_CEILING", 90))        # at/above -> stop
SURPLUS_BELOW  = int(os.environ.get("NIGHTCAP_SURPLUS_BELOW", 50))  # below   -> ramp up
THROTTLE_ABOVE = int(os.environ.get("NIGHTCAP_THROTTLE_ABOVE", 75)) # at/above -> throttle


def skip(why):
    print(f"skip: {why}")
    sys.exit(0)


def run(band, model):
    print(f"run: {band} band, {model} model")
    sys.exit(0)


# fail closed: no trustworthy reading -> do not run
if not os.path.exists(SNAPSHOT):
    skip("no snapshot yet")
try:
    # utf-8-sig tolerates a BOM if some tool wrote one; fine without it too
    with open(SNAPSHOT, encoding="utf-8-sig") as f:
        s = json.load(f)
except Exception:
    skip("snapshot unreadable")

try:
    captured = datetime.fromisoformat(s["captured_at"].replace("Z", "+00:00"))
    age_min = (datetime.now(timezone.utc) - captured).total_seconds() / 60
except Exception:
    skip("snapshot timestamp unreadable")

if age_min < STALE_MIN:
    skip(f"you look active ({age_min:.0f}m old)")
if age_min > STALE_MAX_H * 60:
    skip(f"snapshot {age_min / 60:.1f}h old, failing closed")

# the controller: rolling weekly % maps straight to how hard to run
weekly = (s.get("seven_day") or {}).get("used_percentage")
if weekly is None:
    run("normal", "base")            # can't tell -> neither ramp nor throttle
if weekly >= CEILING:
    skip(f"weekly {weekly}% >= {CEILING}%")
if weekly < SURPLUS_BELOW:
    run("surplus", "big")            # ramp: spend the headroom
if weekly >= THROTTLE_ABOVE:
    run("throttle", "base")          # light: one lane
run("normal", "base")                # standard batch
