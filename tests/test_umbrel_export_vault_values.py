"""Exercise the patched image's real export scrubber after a vault fill.

Run on the image's Python, like the plugin suite. A developer environment without
Hermes skips these integration checks.
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

if importlib.util.find_spec("hermes_cli") is not None:
    from agent import redact
    from hermes_cli.profiles import _scrub_export_secrets
else:
    redact = None


@unittest.skipIf(redact is None, "Requires the Hermes image")
class ExportAfterVaultFill(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        scope = patch.object(redact, "_vault_scope", return_value=str(self.root))
        scope.start()
        self.addCleanup(scope.stop)
        self.addCleanup(redact.clear_vault_redaction_values)
        for value in ("12", "2030", "Ada"):
            redact.register_vault_redaction_value(value)

    def test_export_preserves_unrelated_values_but_model_output_still_redacts_them(self):
        path = self.root / "agent.json"
        original = '{"name": "Ada", "hue": 120, "notes": "Expires 12/2030"}\n'
        path.write_text(original)
        _scrub_export_secrets(self.root)
        self.assertEqual(path.read_text(), original)
        self.assertEqual(json.loads(path.read_text())["hue"], 120)
        model_output = redact.redact_sensitive_text(original, force=True)
        self.assertIn("«redacted-vault-secret»", model_output)
        self.assertNotIn("Ada", model_output)
        self.assertNotIn("2030", model_output)

    def test_export_still_masks_api_keys_and_secret_envelopes(self):
        key = "sk-" + "aBcDeFgHiJkLmNoPqRsTuVwXyZ" * 2
        path = self.root / "config.json"
        path.write_text(json.dumps({
            "api_key": key,
            "saved": {"encoding": "plain", "value": "opaque-fixture-credential"},
            "hue": 120,
        }))
        _scrub_export_secrets(self.root)
        result = json.loads(path.read_text())
        self.assertNotIn(key, path.read_text())
        self.assertEqual(result["saved"], {"encoding": "plain", "value": "***"})
        self.assertEqual(result["hue"], 120)

    def test_scrubbing_a_staged_symlink_does_not_rewrite_the_source(self):
        source = self.root / "source.json"
        original = '{"saved": {"encoding": "plain", "value": "fixture-credential"}, "hue": 120}'
        source.write_text(original)
        stage = self.root / "staged"
        stage.mkdir()
        link = stage / "config.json"
        link.symlink_to(source)
        _scrub_export_secrets(stage)
        self.assertEqual(source.read_text(), original)
        self.assertFalse(link.is_symlink())
        self.assertEqual(json.loads(link.read_text())["saved"]["value"], "***")


if __name__ == "__main__":
    unittest.main()
