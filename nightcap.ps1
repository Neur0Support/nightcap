<#
  nightcap.ps1 -- decide how hard your overnight agent runs, from your remaining Claude usage.
  Reads the snapshot your status line writes (statusline.js). Prints a plan, or skips.
  Fails closed when the reading can't be trusted. Not a cap. A nightcap.  https://neurosupport.co.nz

  Output: "run: <band> band, <model> model"  or  "skip: <why>"
#>
param(
  [string]$Snapshot   = "$HOME/.claude/usage-snapshot.json",
  [int]$StaleMinutes  = 30,   # fresher than this  -> you look active   -> skip (let yourself work)
  [int]$StaleMaxHours = 12,   # older than this    -> can't trust it    -> FAIL CLOSED
  [int]$Ceiling       = 90,   # at or above this   -> stop, protect your own sessions
  [int]$SurplusBelow  = 50,   # below this         -> ramp up
  [int]$ThrottleAbove = 75    # at or above this   -> throttle to light
)

function Skip($why)        { Write-Host "skip: $why"; exit 0 }
function Run($band,$model)  { Write-Host "run: $band band, $model model"; exit 0 }

# Real OS input-idle in minutes (Windows), or $null if the probe can't read it.
# Reads last keyboard/mouse activity across ANY app -- a far better "are you here?"
# signal than the snapshot's freshness. Needs an interactive desktop session
# (which a logged-on scheduled task has); a service/Session-0 context returns $null.
function Get-IdleMinutes {
  try {
    if (-not ([System.Management.Automation.PSTypeName]'Nightcap.Idle').Type) {
      Add-Type -Namespace Nightcap -Name Idle -MemberDefinition @'
[System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
[System.Runtime.InteropServices.DllImport("user32.dll")]
static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
public static double Minutes() {
  LASTINPUTINFO lii = new LASTINPUTINFO();
  lii.cbSize = (uint)System.Runtime.InteropServices.Marshal.SizeOf(lii);
  if (!GetLastInputInfo(ref lii)) { return -1; }
  uint idleMs = (uint)Environment.TickCount - lii.dwTime;   // 32-bit, matches dwTime
  return idleMs / 60000.0;
}
'@
    }
    $m = [Nightcap.Idle]::Minutes()
    if ($m -lt 0) { return $null }
    return $m
  } catch { return $null }
}

# fail closed: never gate on a budget you cannot trust
if (-not (Test-Path $Snapshot)) { Skip 'no snapshot yet' }
try { $s = Get-Content $Snapshot -Raw | ConvertFrom-Json } catch { Skip 'snapshot unreadable' }
try {
  $captured = [datetime]::Parse($s.captured_at).ToUniversalTime()
  $ageMin   = ((Get-Date).ToUniversalTime() - $captured).TotalMinutes
} catch { Skip 'snapshot timestamp unreadable' }

# activity: real OS idle owns this when we can read it. Snapshot freshness is a
# WEAK proxy -- it only moves when the status line renders in a terminal session,
# so working in the desktop app, a browser, or another project is invisible, and a
# stale-but-frozen snapshot can read as "idle" and fire on top of your live work.
$idle = Get-IdleMinutes
if ($null -ne $idle) {
  if ($idle -lt $StaleMinutes) { Skip ("you're active ({0}m real idle)" -f [math]::Round($idle)) }
  # real idle says you're away -> trust it; a fresh snapshot is GOOD budget data, not a skip
} elseif ($ageMin -lt $StaleMinutes) {
  Skip ("you look active ({0}m old, no real-idle probe)" -f [math]::Round($ageMin))   # proxy fallback
}
if ($ageMin -gt $StaleMaxHours * 60) { Skip ("snapshot {0}h old, failing closed" -f [math]::Round($ageMin / 60, 1)) }

# the controller: rolling weekly % maps straight to how hard to run
$weekly = $s.seven_day.used_percentage
if ($null -eq $weekly)          { Run 'normal' 'base' }                 # can't tell -> neither ramp nor throttle
if ($weekly -ge $Ceiling)       { Skip ("weekly $weekly% >= $Ceiling%") }
if ($weekly -lt $SurplusBelow)  { Run 'surplus' 'big' }                 # ramp: spend the headroom
if ($weekly -ge $ThrottleAbove) { Run 'throttle' 'base' }               # light: one lane
Run 'normal' 'base'                                                      # standard batch
