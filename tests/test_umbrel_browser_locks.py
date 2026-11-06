"""umbrel-browser-cont-init.sh: at container start, the lock a stopped browser left on an agent's screen
profile is cleared, so Hermes opens the browser again; a live browser's lock is left alone."""

import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "umbrel-browser-cont-init.sh"
MARKERS = ["DevToolsActivePort", "SingletonCookie", "SingletonLock", "SingletonSocket"]
OLD_CONTAINER = "f7c4310b1ed7"


class StoppedBrowserLocks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.home = self.root / "data"
        shims = self.root / "bin"
        shims.mkdir()
        # The script drops to hermes with s6-setuidgid; here it stays the test's own user.
        (shims / "s6-setuidgid").write_text('#!/bin/sh\nshift\nexec "$@"\n')
        (shims / "s6-setuidgid").chmod(0o755)
        self.env = {**os.environ, "PATH": f"{shims}{os.pathsep}{os.environ['PATH']}",
                    "HERMES_HOME": str(self.home), "HERMES_PYTHON": sys.executable}

    def profile(self, agent=None, lock=None, home=None):
        """An agent's screen profile with a login in it, and a lock naming ``lock`` (``host-pid``)."""
        base = home or self.home
        profile = (base / "profiles" / agent if agent else base) / "bot-desktop" / "browser-profile"
        (profile / "Default").mkdir(parents=True)
        (profile / "Default" / "Cookies").write_text("signed in")
        if lock:
            self.lock(profile, lock)
        return profile

    def lock(self, profile, lock):
        os.symlink(lock, profile / "SingletonLock")
        os.symlink("12611759528951410768", profile / "SingletonCookie")
        os.symlink("/opt/data/cache/scratch/org.chromium.Chromium.R1SQbY/SingletonSocket", profile / "SingletonSocket")
        (profile / "DevToolsActivePort").write_text("41234\n/devtools/browser/0b9f4c1e\n")

    def start(self):
        return subprocess.run(["sh", str(SCRIPT)], env=self.env, capture_output=True, text=True, check=True).stdout

    def markers(self, profile):
        return sorted(name for name in MARKERS if os.path.lexists(profile / name))

    def browser(self, profile):
        """A live process here whose argv opens ``profile``, as Chromium's does."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", f"--user-data-dir={profile}"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc.pid

    def test_a_lock_another_container_left_is_cleared_on_every_agent_and_the_logins_stay(self):
        main = self.profile(lock=f"{OLD_CONTAINER}-1103")
        wren = self.profile("wren", lock=f"{OLD_CONTAINER}-1187")
        out = self.start()
        for profile in (main, wren):
            self.assertEqual(self.markers(profile), [])
            self.assertEqual((profile / "Default" / "Cookies").read_text(), "signed in")
        self.assertEqual(out.splitlines(), [
            f"[umbrel-browser] Cleared the lock a stopped browser left in {main} ({OLD_CONTAINER}-1103)",
            f"[umbrel-browser] Cleared the lock a stopped browser left in {wren} ({OLD_CONTAINER}-1187)",
        ])

    def test_another_containers_lock_is_cleared_even_when_its_pid_is_alive_here(self):
        main = self.profile()
        self.lock(main, f"{OLD_CONTAINER}-{self.browser(main)}")
        self.start()
        self.assertEqual(self.markers(main), [])

    def test_a_lock_of_this_container_whose_browser_is_gone_is_cleared(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()
        main = self.profile(lock=f"{socket.gethostname()}-{gone.pid}")
        self.start()
        self.assertEqual(self.markers(main), [])

    def test_a_lock_of_this_container_on_a_pid_that_is_not_its_browser_is_cleared(self):
        main = self.profile(lock=f"{socket.gethostname()}-{os.getpid()}")
        self.start()
        self.assertEqual(self.markers(main), [])

    def test_a_live_browser_here_keeps_its_lock(self):
        main = self.profile()
        self.lock(main, f"{socket.gethostname()}-{self.browser(main)}")
        self.assertEqual(self.start(), "")
        self.assertEqual(self.markers(main), MARKERS)

    def test_a_live_browser_on_one_agent_keeps_its_lock_while_anothers_stale_one_is_cleared(self):
        main = self.profile()
        self.lock(main, f"{socket.gethostname()}-{self.browser(main)}")
        wren = self.profile("wren", lock=f"{OLD_CONTAINER}-1187")
        self.start()
        self.assertEqual(self.markers(main), MARKERS)
        self.assertEqual(self.markers(wren), [])

    def test_only_the_screens_profiles_on_the_data_volume(self):
        elsewhere = self.profile(lock=f"{OLD_CONTAINER}-1103", home=self.root / "elsewhere")
        (self.home / "profiles").mkdir(parents=True)
        os.symlink(self.root / "elsewhere", self.home / "profiles" / "away")
        hidden = self.profile(".staging-wren", lock=f"{OLD_CONTAINER}-1187")
        own = self.home / "browser-profile"
        own.mkdir()
        self.lock(own, f"{OLD_CONTAINER}-1201")
        self.assertEqual(self.start(), "")
        for profile in (elsewhere, hidden, own):
            self.assertEqual(self.markers(profile), MARKERS)

    def test_nothing_to_do_without_a_lock_or_any_agent(self):
        self.assertEqual(self.start(), "")
        main = self.profile()
        self.assertEqual(self.start(), "")
        self.assertEqual(self.markers(main), [])
        self.assertEqual((main / "Default" / "Cookies").read_text(), "signed in")


if __name__ == "__main__":
    unittest.main()
