#!/usr/bin/env python3
"""Daily supervisor for the 0DTE PAPER agent on the NAS.

The agent's own `loop()` runs one session and SELF-EXITS at the 15:50 ET EOD
flatten (see src/agent.py: `loop()` returns after `_flatten()`). This supervisor
simply (re)launches it inside the trading window each weekday and idles the rest
of the time, so nothing spins overnight or on weekends. `restart: unless-stopped`
in compose brings the whole thing back after a NAS reboot.

Design choices that matter:
  * PAPER only — launches `src.agent --loop --mode paper`. `--loop` (never
    `--once`) is required so the end-of-day broker orphan sweep runs.
  * The window ENDS at 15:50 so we never relaunch on top of the agent's own EOD
    exit (which would tight-loop until 16:00).
  * If the agent dies early (crash), we re-evaluate after 5 min and relaunch —
    a mid-session crash self-heals. A crash-loop just retries every 5 min, logged.
  * No orders are ever placed here; all trading is inside the agent process.
"""
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
WIN_START = (9, 20)     # ~10 min before the 09:30 open; the agent no-gos pre-open
WIN_END = (15, 50)      # the agent self-flattens at 15:50 — do not relaunch past it
APP_DIR = "/app"
# Which config the agent loads. Unset => src.agent's default config.json. A
# container can point AGENT_CONFIG at a different file to run a variant off the
# SAME image, differing only by that config and its own .env.
AGENT_CONFIG = os.environ.get("AGENT_CONFIG", "").strip()


def _agent_cmd() -> list:
    cmd = [sys.executable, "-m", "src.agent", "--loop", "--mode", "paper"]
    if AGENT_CONFIG:
        cmd += ["--config", AGENT_CONFIG]
    return cmd

_child: "subprocess.Popen | None" = None


def log(msg: str) -> None:
    print(f"[supervisor {datetime.now(ET):%Y-%m-%d %H:%M:%S %Z}] {msg}", flush=True)


def _forward(signum, _frame):
    """Forward docker stop/SIGTERM to the child agent so it shuts down cleanly."""
    log(f"received signal {signum}; forwarding to agent and exiting")
    if _child is not None and _child.poll() is None:
        try:
            _child.send_signal(signum)
            _child.wait(timeout=20)
        except Exception:
            pass
    sys.exit(0)


signal.signal(signal.SIGTERM, _forward)
signal.signal(signal.SIGINT, _forward)


def _in_window(now: datetime) -> bool:
    if now.weekday() >= 5:          # Sat / Sun
        return False
    hm = (now.hour, now.minute)
    return WIN_START <= hm < WIN_END


def _next_start(now: datetime) -> datetime:
    t = now.replace(hour=WIN_START[0], minute=WIN_START[1], second=0, microsecond=0)
    while t <= now or t.weekday() >= 5:
        t = (t + timedelta(days=1)).replace(
            hour=WIN_START[0], minute=WIN_START[1], second=0, microsecond=0)
    return t


def main() -> None:
    global _child
    log("started — PAPER-only 0DTE agent supervisor (America/New_York)")
    while True:
        now = datetime.now(ET)
        if _in_window(now):
            cmd = _agent_cmd()
            log("launching: " + " ".join(cmd[1:]))
            _child = subprocess.Popen(cmd, cwd=APP_DIR)
            rc = _child.wait()
            _child = None
            log(f"agent exited rc={rc}; re-evaluating in 5 min")
            time.sleep(300)
        else:
            nxt = _next_start(now)
            secs = max(60.0, (nxt - now).total_seconds())
            log(f"idle — next session launch {nxt:%Y-%m-%d %H:%M %Z} "
                f"(~{secs / 3600:.1f}h away)")
            time.sleep(min(secs, 3600.0))   # cap at 1h so DST / clock drift re-checks


if __name__ == "__main__":
    main()
