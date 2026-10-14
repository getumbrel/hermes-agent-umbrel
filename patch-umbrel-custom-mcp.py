#!/usr/bin/env python3
"""Let agents add MCP servers that aren't in Hermes's catalog.

Hermes 0.22's manage_connections tool tells agents never to hand-edit
mcp_servers and to use it instead, but it only installs catalog entries. An
agent given a server that isn't in the catalog (umbrelOS's own, at
http://10.21.0.1/mcp) is refused by the tool and forbidden the config, so it
connects for the one chat and never saves it. Both the tool's description and
its refusal now say how to add such a server: with `hermes config set`, which
writes config.yaml safely and puts the secret in .env.
"""

from pathlib import Path

TOOL_PATH = Path("/opt/hermes/tools/connectors/tool.py")
MCP_PATH = Path("/opt/hermes/tools/connectors/mcp.py")

CUSTOM = (
    "add it with `hermes config set mcp_servers.<name>.url <url>` (and "
    "`mcp_servers.<name>.headers.<Header> <value>`; put a secret in .env with "
    "`hermes config set <ENV_NAME> <value>` and write the header as `${ENV_NAME}`), "
    "then run /reload-mcp"
)

TOOL_OLD = (
    '"They show the user an approval card and block until it settles. Never hand-edit "\n'
    '        "mcp_servers config — always use this tool. '
)
TOOL_NEW = (
    '"They show the user an approval card and block until it settles. Use this tool rather "\n'
    '        "than hand-editing mcp_servers for catalog servers. A server the user gives you that isn\'t "\n'
    f'        "in the catalog (install refuses it as unknown) is not this tool\'s: {CUSTOM}. '
)

MCP_OLD = (
    '            f"catalog entries only: {\', \'.join(catalog) or \'(empty catalog)\'}."\n'
)
MCP_NEW = (
    '            f"catalog entries only: {\', \'.join(catalog) or \'(empty catalog)\'}."\n'
    f'            " For a server that isn\'t in the catalog, {CUSTOM}."\n'
)


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"Expected upstream text exactly once in {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    replace_once(TOOL_PATH, TOOL_OLD, TOOL_NEW)
    replace_once(MCP_PATH, MCP_OLD, MCP_NEW)


if __name__ == "__main__":
    main()
