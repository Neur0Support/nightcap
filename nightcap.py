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
import shutil
import subprocess
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


def os_idle_minutes():
    """Real OS input-idle (last keyboard/mouse activity, any app), in minutes.

    A far better "are you actually away?" signal than the snapshot's freshness.
    Returns None if it can't read -- the caller then falls back to the freshness
    proxy. Windows is verified; macOS (ioreg) and Linux (xprintidle) are
    best-effort and UNCONFIRMED -- a PR confirming them is welcome. Any failure
    returns None, so a wrong guess never fires the agent.
    """
    try:
        if sys.platform == "win32":
            import ctypes

            class _LII(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            lii = _LII()
            lii.cbSize = ctypes.sizeof(_LII)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                return None
            tick = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
            return ((tick - lii.dwTime) & 0xFFFFFFFF) / 60000.0   # 32-bit wrap-safe
        if sys.platform == "darwin":
            import re
            out = subprocess.run(["ioreg", "-c", "IOHIDSystem"],
                                 capture_output=True, text=True, timeout=5).stdout
            m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
            return int(m.group(1)) / 1e9 / 60.0 if m else None   # nanoseconds -> min
        exe = shutil.which("xprintidle")                          # Linux (X11)
        if exe:
            out = subprocess.run([exe], capture_output=True, text=True, timeout=5).stdout
            return int(out.strip()) / 60000.0                     # ms -> minutes
        return None
    except Exception:
        return None


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

# activity: real OS idle owns this when we can read it. Snapshot freshness is a
# WEAK proxy -- it only moves when the status line renders in a terminal session,
# so a late night in the desktop app, a browser, or another project is invisible,
# and a stale-but-frozen snapshot can read as "idle" and fire on your live work.
idle = os_idle_minutes()
if idle is not None:
    if idle < STALE_MIN:
        skip(f"you're active ({idle:.0f}m real idle)")
    # real idle says you're away -> trust it; a fresh snapshot is good budget data
elif age_min < STALE_MIN:
    skip(f"you look active ({age_min:.0f}m old, no real-idle probe)")   # proxy fallback
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
