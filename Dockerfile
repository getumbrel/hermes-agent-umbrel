# Hermes Agent on Umbrel
# Thin Umbrel context layer on the official Hermes Agent image

# To update: docker buildx imagetools inspect nousresearch/hermes-agent:v2026.6.19
FROM nousresearch/hermes-agent:v2026.6.19@sha256:9f367c7756ef087661a361536a89f438d57a122b958dc23d82d456b1433e6e9e

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

# Umbrel always remaps the upstream hermes user to UID/GID 1000 at container
# boot. Bake ownership of the large runtime trees into the image so upgrades
# do not spend several minutes chowning them before the dashboard can start.
RUN groupmod -o -g 1000 hermes && \
    usermod -u 1000 -g 1000 hermes && \
    chown -R hermes:hermes \
    /opt/hermes/.venv \
    /opt/hermes/ui-tui \
    /opt/hermes/gateway \
    /opt/hermes/node_modules

# Preserve the upstream /init entrypoint and command wrapper.
EXPOSE 18789
