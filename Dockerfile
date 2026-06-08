# Hermes Agent on Umbrel
# Thin Umbrel context layer on the official Hermes Agent image

# To update: docker buildx imagetools inspect nousresearch/hermes-agent:v2026.6.5
FROM nousresearch/hermes-agent:v2026.6.5@sha256:9ad3b04ec916ea2c2da22358fd43b024c788d74073210695af88bfc2e63869b4

USER root

# Bundle Umbrel-managed context in the image. Hermes enables the runtime plugin
# and external skill directory from config.yaml at startup; the files themselves
# stay outside /opt/data so the agent cannot edit managed instructions.
COPY bootstrap-umbrel-context.sh umbrel-setup-wrapper.py patch-umbrel-update-message.py /app/
RUN mkdir -p /app/umbrel-tui/dist
COPY umbrel-chat-tui-entry.js /app/umbrel-tui/dist/entry.js
COPY umbrel-context-cont-init.sh /etc/cont-init.d/50-umbrel-context
COPY umbrel-context/skills /app/umbrel-context/skills
COPY umbrel-context/plugins/umbrel-runtime /opt/hermes/plugins/umbrel-runtime
RUN /opt/hermes/.venv/bin/python /app/patch-umbrel-update-message.py && rm /app/patch-umbrel-update-message.py
RUN chmod +x /app/bootstrap-umbrel-context.sh /app/umbrel-setup-wrapper.py /etc/cont-init.d/50-umbrel-context

# Preserve the upstream /init entrypoint and command wrapper.
EXPOSE 18789
