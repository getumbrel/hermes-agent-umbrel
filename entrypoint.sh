#!/bin/bash
# Start the web container: enable managed Umbrel context, run the Hermes
# dashboard locally, then serve the proxied terminal/dashboard UI.
set -e

source "/opt/hermes/.venv/bin/activate"

/app/bootstrap-umbrel-context.sh

# Start dashboard in background.
# Binds to 127.0.0.1 (not 0.0.0.0) since our Node server proxies it — no
# direct external access needed, and avoids the "Binding to 0.0.0.0" warning.
# If it crashes, the terminal still works — user can restart the app from Umbrel.
hermes dashboard --host 127.0.0.1 --no-open &

exec node /app/server.cjs
