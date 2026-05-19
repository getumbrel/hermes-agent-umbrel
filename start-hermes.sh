#!/bin/bash
# Terminal entrypoint: enable managed Umbrel context, run setup only when
# Hermes' own runtime provider resolver cannot find usable credentials, then
# start chat and leave users in a shell for follow-up admin commands.

/app/bootstrap-umbrel-context.sh

if ! python3 - <<'PY' 2>/dev/null; then
from dotenv import load_dotenv
from hermes_cli.runtime_provider import resolve_runtime_provider

load_dotenv("/opt/data/.env")
resolve_runtime_provider()
PY
  hermes setup
fi

/app/bootstrap-umbrel-context.sh

hermes

exec bash
