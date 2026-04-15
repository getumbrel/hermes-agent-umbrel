#!/bin/bash
# Activate the hermes Python venv and start the terminal server.
# Config bootstrapping (directory creation, .env/.yaml copying, skills sync)
# is handled by the gateway container's official entrypoint.
set -e

source "/opt/hermes/.venv/bin/activate"

exec node /app/server.cjs
