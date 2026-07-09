# Hermes Agent on Umbrel
# Thin Umbrel context layer on the official Hermes Agent image

# To update: docker buildx imagetools inspect nousresearch/hermes-agent:v2026.7.7.2
FROM nousresearch/hermes-agent:v2026.7.7.2@sha256:9c841866021c54c4596849f6135717e8a4d52ba510b7f52c50aef1de1a283973

USER root

# Bundle Umbrel-managed context in the image. Hermes enables the runtime plugin
# and external skill directory from config.yaml at startup; the files themselves
# stay outside /opt/data so the agent cannot edit managed instructions.
COPY bootstrap-umbrel-context.sh umbrel-setup-wrapper.py patch-umbrel-update-message.py patch-umbrel-proxy-auth.py /app/
RUN mkdir -p /app/umbrel-tui/dist
COPY umbrel-chat-tui-entry.js /app/umbrel-tui/dist/entry.js
COPY umbrel-context-cont-init.sh /etc/cont-init.d/50-umbrel-context
COPY umbrel-context/skills /app/umbrel-context/skills
COPY umbrel-context/plugins/umbrel-runtime /opt/hermes/plugins/umbrel-runtime
RUN /opt/hermes/.venv/bin/python /app/patch-umbrel-update-message.py && rm /app/patch-umbrel-update-message.py
# Patch only the Umbrel image layer. Upstream Hermes still requires dashboard
# auth by default; this image can opt into trusting umbrelOS app-proxy auth.
RUN /opt/hermes/.venv/bin/python /app/patch-umbrel-proxy-auth.py && rm /app/patch-umbrel-proxy-auth.py
RUN chmod +x /app/bootstrap-umbrel-context.sh /app/umbrel-setup-wrapper.py /etc/cont-init.d/50-umbrel-context

# Umbrel runs the upstream hermes user as UID/GID 1000. Bake that identity into
# the image, but keep the upstream /opt/hermes install tree root-owned and
# non-writable so v0.17+ Docker hardening remains intact.
RUN groupmod -o -g 1000 hermes && \
    usermod -u 1000 -g 1000 hermes

# Preserve the upstream /init entrypoint and command wrapper.
EXPOSE 18789
