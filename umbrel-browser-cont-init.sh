#!/command/with-contenv sh
# Let an agent's browser open again once its container has been replaced.
#
# Hermes keeps the browser on each agent's screen in a profile on the data
# volume: <HERMES_HOME>/bot-desktop/browser-profile, and the same under
# profiles/<name>/. umbrelOS makes a new container on every restart, reboot and
# update, and the browser stopped with the old one leaves Chromium's
# SingletonLock behind, naming that container. Hermes reads it as a browser
# still running in another environment and won't open the profile from then
# on: https://github.com/NousResearch/hermes-agent/issues/147739
#
# A lock is stale unless it names this container's hostname and a process here
# whose argv opens this profile (Hermes's own test for a live browser). Clear a
# stale one and the markers that go with it, as Hermes does for a cloned
# profile (_CLONE_ALL_STRIP in hermes_cli/profiles.py); the profile's logins
# and cookies stay. Only the screens' profiles on the data volume, as hermes,
# who owns them. Drop this file and its COPY line in the Dockerfile once
# upstream fixes that issue.

exec s6-setuidgid hermes "${HERMES_PYTHON:-/opt/hermes/.venv/bin/python}" - "${HERMES_HOME:-/opt/data}" <<'PY'
import os
import socket
import sys
from pathlib import Path

home = Path(sys.argv[1])
here = socket.gethostname()
MARKERS = ("SingletonLock", "SingletonCookie", "SingletonSocket", "DevToolsActivePort")


def opened_here(pid: str, profile: Path) -> bool:
    """A process of this container whose argv opens ``profile``: the browser that holds it."""
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    flags = (os.fsdecode(arg) for arg in argv)
    return any(flag.startswith("--user-data-dir=") and Path(flag.split("=", 1)[1]).resolve() == profile.resolve()
               for flag in flags)


agents = sorted(p for p in (home / "profiles").glob("*") if not p.name.startswith("."))
for profile in [home / "bot-desktop" / "browser-profile", *(a / "bot-desktop" / "browser-profile" for a in agents)]:
    try:
        lock = os.readlink(profile / "SingletonLock")
    except OSError:
        continue
    try:
        if not profile.resolve().is_relative_to(home.resolve()):
            continue
        host, _, pid = lock.rpartition("-")
        if host == here and pid.isdigit() and opened_here(pid, profile):
            continue
        for name in MARKERS:
            try:
                (profile / name).unlink()
            except FileNotFoundError:
                pass
        print(f"[umbrel-browser] Cleared the lock a stopped browser left in {profile} ({lock})", flush=True)
    except OSError as exc:
        print(f"[umbrel-browser] Warning: could not clear the lock in {profile}: {exc}", file=sys.stderr)
PY
