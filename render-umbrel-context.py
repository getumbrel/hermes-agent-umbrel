#!/usr/bin/env python3
"""Render the managed Umbrel skills from their template for this app.

The same image runs as the standalone "Hermes Agent" Umbrel app and as the
built-in Hermes of other Umbrel apps (e.g. Murmur), so user-facing umbrelOS
paths in the skill name the app from UMBREL_APP_NAME. Runs at image build (with
the default name) and again as root from cont-init before Hermes starts, so the
rendered skills stay root-owned, read-only to the agent and never persisted.
"""
import os
from pathlib import Path
import shutil
import sys

DEFAULT_APP_NAME = "Hermes Agent"
PLACEHOLDER = "{{UMBREL_APP_NAME}}"


def app_name():
    name = " ".join(os.environ.get("UMBREL_APP_NAME", "").split())
    return name or DEFAULT_APP_NAME


def render(template_dir: Path, output_dir: Path, name: str):
    # Write files in place: overlayfs cannot rename directories that come from
    # an image layer, and nothing reads these before cont-init finishes.
    wanted = set()
    for source in sorted(template_dir.rglob("*")):
        relative = source.relative_to(template_dir)
        target = output_dir / relative
        wanted.add(relative)
        if source.is_dir():
            target.mkdir(mode=0o755, parents=True, exist_ok=True)
            continue
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        content = source.read_bytes()
        if source.suffix == ".md":
            content = content.decode("utf-8").replace(PLACEHOLDER, name).encode("utf-8")
        if not target.is_file() or target.read_bytes() != content:
            target.write_bytes(content)
        target.chmod(0o644)

    for target in sorted(output_dir.rglob("*"), reverse=True):
        if target.relative_to(output_dir) in wanted:
            continue
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()


if __name__ == "__main__":
    template_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/app/umbrel-context-template/skills")
    output_dir = Path(sys.argv[2] if len(sys.argv) > 2 else "/app/umbrel-context/skills")
    name = app_name()
    render(template_dir, output_dir, name)
    print(f"Umbrel context: rendered skills for {name}")
