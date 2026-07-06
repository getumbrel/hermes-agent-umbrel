#!/usr/bin/env python3
"""Allow the Umbrel image to rely on umbrelOS app-proxy authentication."""

from pathlib import Path
import os


WEB_SERVER_PATH = Path(
    os.environ.get("HERMES_WEB_SERVER_PATH", "/opt/hermes/hermes_cli/web_server.py")
)
FUNCTION_START = "def should_require_auth(host: str, allow_public: bool = False) -> bool:"
RETURN_LINE = "    return host not in _LOOPBACK_HOST_VALUES\n"
BYPASS = '''    # Umbrel apps are only exposed to users through the umbrelOS app proxy,
    # which already enforces the user's Umbrel session before forwarding to
    # this container. Hermes cannot detect that trusted outer auth boundary from
    # inside Docker, so its public-bind dashboard gate would otherwise force a
    # redundant second login. Keep this opt-in and Umbrel-specific: setting it
    # outside the app proxy makes the dashboard unauthenticated.
    if os.environ.get("HERMES_UMBREL_APP_PROXY_AUTH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
'''


def main() -> None:
    text = WEB_SERVER_PATH.read_text(encoding="utf-8")
    start = text.find(FUNCTION_START)
    if start == -1:
        raise RuntimeError("Expected upstream should_require_auth() not found")
    insert_at = text.find(RETURN_LINE, start)
    if insert_at == -1:
        raise RuntimeError("Expected should_require_auth() return line not found")
    if BYPASS in text[start:insert_at]:
        raise RuntimeError("Umbrel proxy auth bypass already applied")
    WEB_SERVER_PATH.write_text(
        text[:insert_at] + BYPASS + text[insert_at:],
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
