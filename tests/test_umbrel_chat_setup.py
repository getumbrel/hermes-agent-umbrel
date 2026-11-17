"""The dashboard Chat launcher should follow Hermes's configured fallback chain."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ENTRY = Path("/app/umbrel-tui/dist/entry.js")


@unittest.skipUnless(ENTRY.is_file(), "Requires the Hermes wrapper image")
class ChatSetup(unittest.TestCase):
    def test_missing_primary_credentials_with_usable_fallback_opens_chat(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "config.yaml").write_text(
                "model:\n  provider: nous\n  default: primary\n"
                "fallback_providers:\n  - provider: custom\n    model: fallback\n"
                "    base_url: http://127.0.0.1:9999/v1\n"
            )
            tui = home / "test-entry.js"
            tui.write_text('console.log("reached the upstream TUI")')
            result = subprocess.run(
                ["node", str(ENTRY)], stdin=subprocess.DEVNULL, capture_output=True,
                text=True, timeout=20, env={
                    "PATH": os.environ["PATH"], "HOME": tmp, "HERMES_HOME": tmp,
                    "HERMES_UPSTREAM_TUI_ENTRY": str(tui),
                },
            )
            self.assertNotIn("Starting setup now.", result.stdout)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("reached the upstream TUI", result.stdout)
