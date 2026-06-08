#!/usr/bin/env python3
"""Patch upstream Docker update guidance for the Umbrel image."""

from pathlib import Path


CONFIG_PATH = Path("/opt/hermes/hermes_cli/config.py")
UMBREL_UPDATE_MESSAGE = "Hermes updates are managed by umbrelOS app updates."

DOCKER_COMMAND_OLD = (
    'if method == "docker":\n'
    '        return "docker pull nousresearch/hermes-agent:latest"'
)
DOCKER_COMMAND_NEW = (
    'if method == "docker":\n'
    '        return ""'
)
MESSAGE_START = '_DOCKER_UPDATE_MESSAGE = """\\\n'
MESSAGE_END = "\n\n\ndef format_docker_update_message"


def replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        raise RuntimeError(f"Expected upstream text not found: {old!r}")
    return text.replace(old, new, 1)


def main() -> None:
    text = CONFIG_PATH.read_text(encoding="utf-8")

    text = replace_once(text, DOCKER_COMMAND_OLD, DOCKER_COMMAND_NEW)

    start = text.find(MESSAGE_START)
    if start == -1:
        raise RuntimeError("Expected upstream Docker update message start not found")

    end = text.find(MESSAGE_END, start)
    if end == -1:
        raise RuntimeError("Expected upstream Docker update message end not found")

    replacement = f'_DOCKER_UPDATE_MESSAGE = "{UMBREL_UPDATE_MESSAGE}"'
    text = text[:start] + replacement + text[end:]

    CONFIG_PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
