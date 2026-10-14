#!/command/with-contenv sh
# Reconcile Umbrel-managed Hermes context when this image is launched through
# the upstream s6 entrypoint (/init).
set -e

# Render the managed skills for this app's name (UMBREL_APP_NAME) as root so
# they stay read-only to the agent, then reconcile every agent's config as the
# hermes user.
/opt/hermes/.venv/bin/python /app/render-umbrel-context.py

exec s6-setuidgid hermes /app/bootstrap-umbrel-context.sh
