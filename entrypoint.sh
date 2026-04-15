#!/bin/bash
# Activate the hermes Python venv, start the dashboard, then the terminal server.
# Config bootstrapping (directory creation, .env/.yaml copying, skills sync)
# is handled by the gateway container's official entrypoint.
set -e

source "/opt/hermes/.venv/bin/activate"

# Start dashboard in background.
# Binds to 127.0.0.1 (not 0.0.0.0) since our Node server proxies it — no
# direct external access needed, and avoids the "Binding to 0.0.0.0" warning.
# If it crashes, the terminal still works — user can restart the app from Umbrel.
hermes dashboard --host 127.0.0.1 --no-open &

exec node /app/server.cjs
