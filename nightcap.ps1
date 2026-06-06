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

# fail closed: never gate on a budget you cannot trust
if (-not (Test-Path $Snapshot)) { Skip 'no snapshot yet' }
try { $s = Get-Content $Snapshot -Raw | ConvertFrom-Json } catch { Skip 'snapshot unreadable' }
try {
  $captured = [datetime]::Parse($s.captured_at).ToUniversalTime()
  $ageMin   = ((Get-Date).ToUniversalTime() - $captured).TotalMinutes
} catch { Skip 'snapshot timestamp unreadable' }

if ($ageMin -lt $StaleMinutes)       { Skip ("you look active ({0}m old)" -f [math]::Round($ageMin)) }
if ($ageMin -gt $StaleMaxHours * 60) { Skip ("snapshot {0}h old, failing closed" -f [math]::Round($ageMin / 60, 1)) }

# the controller: rolling weekly % maps straight to how hard to run
$weekly = $s.seven_day.used_percentage
if ($null -eq $weekly)          { Run 'normal' 'base' }                 # can't tell -> neither ramp nor throttle
if ($weekly -ge $Ceiling)       { Skip ("weekly $weekly% >= $Ceiling%") }
if ($weekly -lt $SurplusBelow)  { Run 'surplus' 'big' }                 # ramp: spend the headroom
if ($weekly -ge $ThrottleAbove) { Run 'throttle' 'base' }               # light: one lane
Run 'normal' 'base'                                                      # standard batch
