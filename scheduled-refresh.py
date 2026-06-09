#!/usr/bin/env python3
"""scheduled-refresh.py -- keep the usage snapshot fed, unattended.

The snapshot Nightcap reads is written only by the status line (statusline.js), and the
status line only renders in a real terminal session. So unattended, it goes stale and
Nightcap fails closed. This briefly drives a throwaway interactive Claude session in a
pseudo-terminal, sends one harmless message to force a status-line render carrying the
live rate_limits, waits until the snapshot's weekly % refreshes to a non-null value,
then quits. Schedule it shortly before your run window.

  Success = snapshot captured_at advances AND seven_day.used_percentage is non-null.
  Exit 0 success, 1 timeout, 2 setup error.

  *** The Windows path is verified (pywinpty / ConPTY). The Unix path (stdlib pty) is
      best-effort and UNCONFIRMED -- if you run macOS/Linux, please verify and PR.
      Nightcap fails closed, so a broken refresh just means the next run stands down. ***

Requirements: statusline.js active in your Claude settings, Claude logged in, and on
Windows `pip install pywinpty`. Env: NIGHTCAP_SNAPSHOT (the snapshot path your status
line writes, default ~/.claude/usage-snapshot.json), NIGHTCAP_PROJECT (dir to run
Claude in, default the current directory), NIGHTCAP_CLAUDE_BIN (override: full path to
the Claude executable -- set this if auto-detect can't find it, e.g. when the npm-global
bin shims are virtualized away from a scheduled task; see find_claude()).
"""
import json
import os
import sys
import time
import shutil
import threading

SNAP     = os.environ.get('NIGHTCAP_SNAPSHOT', os.path.expanduser('~/.claude/usage-snapshot.json'))
PROJECT  = os.environ.get('NIGHTCAP_PROJECT', os.getcwd())
TIMEOUT  = 115
MSG_TEXT = 'Reply with the single word: ok. Do not use any tools.'
ACTIONS  = [(16, '\r'), (22, MSG_TEXT), (25, '\r'), (50, '\r'), (80, '\r')]
DISALLOW = 'Bash,PowerShell,Write,Edit,MultiEdit,NotebookEdit'


def read_snap():
    try:
        with open(SNAP, encoding='utf-8-sig') as f:
            s = json.load(f)
        return s.get('captured_at'), (s.get('seven_day') or {}).get('used_percentage')
    except Exception:
        return None, None


def find_claude():
    # 1) explicit override -- the reliable escape hatch on any machine / OS.
    env = os.environ.get('NIGHTCAP_CLAUDE_BIN')
    if env and os.path.exists(env):
        return env
    # 2) On Windows, prefer the REAL native binary inside node_modules. The npm-global
    #    bin shims (claude / claude.cmd / claude.ps1) can be written under a virtualized
    #    or packaged context (e.g. launched from an MSIX terminal) and are then INVISIBLE
    #    to a plain scheduled task -- it sees only node_modules in %APPDATA%\npm, so a
    #    shim lookup returns "not found" and the task exits 2. The real binary is a
    #    normal file the task can read.
    if os.name == 'nt':
        appdata = os.environ.get('APPDATA') or os.path.expanduser(r'~\AppData\Roaming')
        real_exe = os.path.join(appdata, 'npm', 'node_modules', '@anthropic-ai',
                                'claude-code', 'bin', 'claude.exe')
        if os.path.exists(real_exe):
            return real_exe
    # 3) PATH / shim fallbacks (fine on non-virtualized installs, and on Unix where the
    #    virtualization problem does not arise).
    c = shutil.which('claude') or shutil.which('claude.cmd')
    if c:
        return c
    for cand in (os.path.expanduser(r'~\AppData\Roaming\npm\claude.cmd'),
                 os.path.expanduser('~/.npm-global/bin/claude'),
                 '/usr/local/bin/claude', '/opt/homebrew/bin/claude'):
        if os.path.exists(cand):
            return cand
    return None


class _UnixPty:
    """stdlib-pty adapter mirroring the pywinpty surface. Best-effort, unconfirmed."""
    def __init__(self, argv, cwd):
        import pty
        import fcntl
        import subprocess
        self.master, slave = pty.openpty()
        fl = fcntl.fcntl(self.master, fcntl.F_GETFL)
        fcntl.fcntl(self.master, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        self.proc = subprocess.Popen(argv, cwd=cwd, stdin=slave, stdout=slave,
                                     stderr=slave, start_new_session=True, close_fds=True)
        os.close(slave)

    def write(self, s):
        os.write(self.master, s.encode())

    def read(self, n=4096):
        try:
            return os.read(self.master, n).decode(errors='replace')
        except (BlockingIOError, OSError):
            return ''

    def isalive(self):
        return self.proc.poll() is None

    def terminate(self, force=False):
        import signal
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL if force else signal.SIGTERM)
        except Exception:
            pass


def spawn(argv, cwd):
    if os.name == 'nt':
        from winpty import PtyProcess          # verified path
        return PtyProcess.spawn(argv, cwd=cwd, dimensions=(45, 130))
    return _UnixPty(argv, cwd)                 # best-effort path


def main():
    before_cap, _ = read_snap()
    claude = find_claude()
    if not claude:
        print('scheduled-refresh: claude not found', file=sys.stderr)
        return 2

    # A native .exe (or a Unix binary) can be spawned directly; only a Windows .cmd/.ps1
    # shim must go through cmd.exe, because pywinpty cannot exec a batch file.
    if os.name == 'nt' and not claude.lower().endswith('.exe'):
        argv = ['cmd.exe', '/c', claude, '--disallowedTools', DISALLOW]
    else:
        argv = [claude, '--disallowedTools', DISALLOW]
    print('scheduled-refresh: spawning interactive Claude (tools disabled)...')
    proc = spawn(argv, PROJECT)

    buf = []
    def drain():
        while True:
            try:
                d = proc.read(4096)
            except Exception:
                break
            if d:
                buf.append(d)
            else:
                time.sleep(0.2)
                if not proc.isalive():
                    break
    threading.Thread(target=drain, daemon=True).start()

    start = time.time()
    done = set()
    ok = False
    while time.time() - start < TIMEOUT:
        elapsed = time.time() - start
        for i, (at, keys) in enumerate(ACTIONS):
            if i not in done and elapsed > at:
                try:
                    proc.write(keys)
                except Exception:
                    pass
                done.add(i)
        cap, wk = read_snap()
        if cap and cap != before_cap and wk is not None:
            ok = True
            break
        time.sleep(1)

    for keys in ('\x03', '/exit\r'):
        try:
            proc.write(keys)
            time.sleep(0.4)
        except Exception:
            pass
    try:
        if proc.isalive():
            proc.terminate(force=True)
    except Exception:
        pass

    cap, wk = read_snap()
    if ok:
        print(f'scheduled-refresh: OK -- weekly {wk}%, captured {cap}')
        return 0
    print(f'scheduled-refresh: FAILED -- captured_at {cap}, weekly {wk}.', file=sys.stderr)
    print('--- last output ---\n' + ''.join(buf)[-1500:], file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
