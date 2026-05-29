#!/command/with-contenv sh
# Reconcile Umbrel-managed Hermes context when this image is launched through
# the upstream s6 entrypoint (/init), such as the gateway service.
set -e

exec s6-setuidgid hermes /app/bootstrap-umbrel-context.sh
