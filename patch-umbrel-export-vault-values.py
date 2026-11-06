#!/usr/bin/env python3
"""Keep an agent's export as it is after its browser filled a saved card.

A vault fill registers the item's values so they never go back to the model (agent/redact.py,
redact_registered_vault_values), and they stay registered until Hermes restarts. Profile export
force-redacts every text file it stages through the same redact_sensitive_text, so the scrub
meant for model egress ran over the agent's own files too. A card's values include short, common
ones (its expiry month, "12"; its holder's name), so after any card fill an export came back with
`"hue": 120` as `"hue": «redacted-vault-secret»0`, and its name and notes rewritten.

Two changes:

1. agent/redact.py: redact_sensitive_text takes `vault_values` (default True, so every model-bound
   caller keeps the scrub) and skips the registered vault values when it's False.
2. hermes_cli/profiles.py: _scrub_export_secrets passes vault_values=False. Secret-shaped strings
   (keys, tokens) are still scrubbed from the export.

Carried until upstream Hermes ships its own fix; then drop this file and its COPY and RUN lines in
the Dockerfile. Upstream issue: https://github.com/NousResearch/hermes-agent/issues/149596
On a Hermes bump, a failed apply here ("anchor not found") is the signal to check whether upstream
changed or fixed this. Check its release notes even when this applies cleanly: a fix made elsewhere
could leave these lines as they are.
"""

from pathlib import Path
import os


REDACT_PATH = Path(os.environ.get("HERMES_REDACT_PATH", "/opt/hermes/agent/redact.py"))
PROFILES_PATH = Path(os.environ.get("HERMES_PROFILES_PATH", "/opt/hermes/hermes_cli/profiles.py"))

# 1. The keyword, and the scrub it skips.
SIGNATURE = "                          redact_url_credentials: bool = False) -> str:\n"
SIGNATURE_KEYWORD = "                          redact_url_credentials: bool = False, vault_values: bool = True) -> str:\n"
DOCSTRING_END = '    fixtures are untouched.\n    """\n'
DOCSTRING_END_NOTED = '''    fixtures are untouched.

    ``vault_values=False`` skips the exact-value scrub of vault secrets a fill registered. Those
    values guard what goes back to the model, and they include short, common ones (a card's
    expiry month, its holder's name), so a pass over a profile's own files must not apply them.
    """
'''
VAULT_SCRUB = "    text = redact_registered_vault_values(text)\n"
VAULT_SCRUB_UNLESS = """    if vault_values:
        text = redact_registered_vault_values(text)
"""

# 2. The export asks for it.
EXPORT_SCRUB = "        redacted = redact_sensitive_text(text, force=True)\n"
EXPORT_SCRUB_KEEPS = """        # Not the vault values a browser fill registered: they guard model egress, and a card's
        # expiry month or holder's name would rewrite unrelated text across the whole profile.
        redacted = redact_sensitive_text(text, force=True, vault_values=False)
"""


def replace_once(text: str, old: str, new: str, what: str) -> str:
    if new in text:
        raise RuntimeError(f"{what} already applied")
    if text.count(old) != 1:
        raise RuntimeError(f"Expected upstream {what} anchor not found exactly once")
    return text.replace(old, new)


def main() -> None:
    text = REDACT_PATH.read_text(encoding="utf-8")
    text = replace_once(text, SIGNATURE, SIGNATURE_KEYWORD, "redact_sensitive_text vault_values keyword")
    text = replace_once(text, DOCSTRING_END, DOCSTRING_END_NOTED, "redact_sensitive_text docstring")
    text = replace_once(text, VAULT_SCRUB, VAULT_SCRUB_UNLESS, "vault value scrub")
    REDACT_PATH.write_text(text, encoding="utf-8")

    text = PROFILES_PATH.read_text(encoding="utf-8")
    text = replace_once(text, EXPORT_SCRUB, EXPORT_SCRUB_KEEPS, "profile export scrub")
    PROFILES_PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
