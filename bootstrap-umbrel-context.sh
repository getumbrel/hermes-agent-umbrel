#!/bin/bash
# Enable the bundled Umbrel runtime plugin and skill directory in Hermes config.
# This stays idempotent so repeated container boots can run it safely.
set -euo pipefail

DATA_DIR="${HERMES_HOME:-/opt/data}"
SKILLS_SOURCE="/app/umbrel-context/skills"
CONFIG_FILE="${DATA_DIR}/config.yaml"
PLUGIN_NAME="umbrel-runtime"
HERMES_PYTHON="${HERMES_PYTHON:-/opt/hermes/.venv/bin/python}"

if [[ ! -d "${DATA_DIR}" ]]; then
  echo "Umbrel context: ${DATA_DIR} does not exist; persistent data volume is not mounted" >&2
  exit 1
fi

"${HERMES_PYTHON}" - "${CONFIG_FILE}" "${SKILLS_SOURCE}" "${PLUGIN_NAME}" <<'PY'
from pathlib import Path
import re
import sys
import yaml

config_path = Path(sys.argv[1])
skills_dir = sys.argv[2]
plugin_name = sys.argv[3]

text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
lines = text.splitlines(keepends=True)

def is_top_level_key(line: str) -> bool:
    return re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:\s*(?:#.*)?$", line) is not None

def section_bounds(section: str):
    start = next(
        (i for i, line in enumerate(lines) if re.match(rf"^{re.escape(section)}:\s*(?:#.*)?$", line)),
        None,
    )
    if start is None:
        return None, None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if is_top_level_key(lines[i]):
            end = i
            break
    return start, end

def ensure_child_list_item(section: str, child: str, item: str):
    global lines
    section_index, section_end = section_bounds(section)
    if section_index is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        if lines and lines[-1].strip():
            lines.append("\n")
        lines.extend([
            f"{section}:\n",
            f"  {child}:\n",
            f"    - {item}\n",
        ])
        return

    child_index = None
    child_match = None
    child_re = re.compile(rf"^(  {re.escape(child)}:)([ \t]*)(.*?)([ \t]*(?:#.*)?)$")
    for i in range(section_index + 1, section_end):
        match = child_re.match(lines[i].rstrip("\n"))
        if match:
            child_index = i
            child_match = match
            break

    if child_index is None:
        lines[section_index + 1:section_index + 1] = [
            f"  {child}:\n",
            f"    - {item}\n",
        ]
        return

    inline_value = (child_match.group(3) if child_match else "").strip()
    if inline_value:
        try:
            existing = yaml.safe_load(inline_value)
        except Exception:
            existing = []
        if not isinstance(existing, list):
            existing = []
        rendered = [f"  {child}:\n"]
        seen = set()
        for value in existing + [item]:
            value = str(value)
            if value in seen:
                continue
            seen.add(value)
            rendered.append(f"    - {value}\n")
        lines[child_index:child_index + 1] = rendered
        return

    item_re = re.compile(rf"^\s*-\s*{re.escape(item)}\s*(?:#.*)?$")
    list_indent = None
    insert_at = child_index + 1
    while insert_at < section_end:
        line = lines[insert_at]
        if item_re.match(line):
            return
        list_match = re.match(r"^(\s*)-\s+", line)
        if list_match:
            list_indent = list_match.group(1)
            insert_at += 1
            continue
        if re.match(r"^\s*$", line) or re.match(r"^\s*#", line):
            insert_at += 1
            continue
        break
    if list_indent is None:
        list_indent = "    "
    lines.insert(insert_at, f"{list_indent}- {item}\n")

def remove_child_list_item(section: str, child: str, item: str):
    global lines
    section_index, section_end = section_bounds(section)
    if section_index is None:
        return

    child_index = None
    child_match = None
    child_re = re.compile(rf"^(  {re.escape(child)}:)([ \t]*)(.*?)([ \t]*(?:#.*)?)$")
    for i in range(section_index + 1, section_end):
        match = child_re.match(lines[i].rstrip("\n"))
        if match:
            child_index = i
            child_match = match
            break

    if child_index is None:
        return

    inline_value = (child_match.group(3) if child_match else "").strip()
    if inline_value:
        try:
            existing = yaml.safe_load(inline_value)
        except Exception:
            existing = []
        if not isinstance(existing, list):
            existing = []
        remaining = [str(value) for value in existing if str(value) != item]
        if remaining:
            rendered = [f"  {child}:\n"]
            for value in remaining:
                rendered.append(f"    - {value}\n")
            lines[child_index:child_index + 1] = rendered
        else:
            lines[child_index] = f"  {child}: []\n"
        return

    item_re = re.compile(rf"^\s*-\s*{re.escape(item)}\s*(?:#.*)?$")
    i = child_index + 1
    removed_any = False
    while i < section_end:
        line = lines[i]
        if item_re.match(line):
            del lines[i]
            section_end -= 1
            removed_any = True
            continue
        if re.match(r"^\s*-\s+", line) or re.match(r"^\s*$", line) or re.match(r"^\s*#", line):
            i += 1
            continue
        break

    if removed_any:
        has_items = False
        i = child_index + 1
        while i < section_end:
            line = lines[i]
            if re.match(r"^\s*-\s+", line):
                has_items = True
                break
            if re.match(r"^\s*$", line) or re.match(r"^\s*#", line):
                i += 1
                continue
            break
        if not has_items:
            lines[child_index] = f"  {child}: []\n"

if Path(skills_dir).is_dir():
    ensure_child_list_item("skills", "external_dirs", skills_dir)
else:
    print(f"Umbrel context: {skills_dir} not found; skill directory will be reconciled on next startup", file=sys.stderr)

ensure_child_list_item("plugins", "enabled", plugin_name)
remove_child_list_item("plugins", "disabled", plugin_name)

new_text = "".join(lines)
config_path.write_text(new_text, encoding="utf-8")
PY
