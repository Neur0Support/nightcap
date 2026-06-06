#!/usr/bin/env node
/*
 * statusline.js -- Claude Code status line + usage snapshot writer.
 *
 * Configure as your statusLine command in ~/.claude/settings.json. Claude Code
 * pipes the status-line JSON to this script on stdin every render. We (1) write
 * the subscription rate-limit state to a snapshot file that nightcap reads, and
 * (2) print a compact "5h | wk" usage string so you can see your budget live.
 *
 * rate_limits appears only for Pro/Max subscribers, and only after the first API
 * response in a session (absent on the very first render). Fail-open: this script
 * never throws and never blocks the UI.
 */
const fs = require('fs');
const os = require('os');
const path = require('path');

const SNAPSHOT = process.env.NIGHTCAP_SNAPSHOT
  || path.join(os.homedir(), '.claude', 'usage-snapshot.json');

const pct = (n) => (typeof n === 'number' ? Math.round(n) + '%' : '-');

let line = 'usage -';
try {
  let raw = fs.readFileSync(0, 'utf8');
  if (raw.charCodeAt(0) === 0xFEFF) raw = raw.slice(1); // strip BOM some shells prepend
  const data = JSON.parse(raw);
  const rl = data.rate_limits || {};
  const five = rl.five_hour || {};
  const week = rl.seven_day || {};

  const snap = {
    captured_at: new Date().toISOString(),
    five_hour: {
      used_percentage: typeof five.used_percentage === 'number' ? five.used_percentage : null,
      resets_at: typeof five.resets_at === 'number' ? five.resets_at : null,
    },
    seven_day: {
      used_percentage: typeof week.used_percentage === 'number' ? week.used_percentage : null,
      resets_at: typeof week.resets_at === 'number' ? week.resets_at : null,
    },
  };
  try { fs.writeFileSync(SNAPSHOT, JSON.stringify(snap, null, 2)); } catch (_) { /* best effort */ }

  const usage = '5h ' + pct(five.used_percentage) + ' | wk ' + pct(week.used_percentage);
  const model = data.model && data.model.display_name ? data.model.display_name : '';
  line = model ? model + ' | ' + usage : usage;
} catch (_) {
  line = 'usage -';
}
process.stdout.write(line);
