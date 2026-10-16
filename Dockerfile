# Hermes Agent on Umbrel
# Thin Umbrel context layer on the official Hermes Agent image

# To update: docker buildx imagetools inspect nousresearch/hermes-agent:v0.22.2
FROM nousresearch/hermes-agent:v0.22.2@sha256:36099926da316742423544e055d4734777105ffe6341873af5459b957d0c2919

USER root

# GPU acceleration for Hermes' built-in local model runtime. On Intel and AMD
# GPUs Hermes installs llama.cpp's Vulkan build, which needs the Vulkan loader
# and Mesa's drivers; without them it silently falls back to the CPU. NVIDIA's
# CUDA and Vulkan userspace comes from the host's container toolkit when
# umbrelOS grants the app the GPU permission.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libvulkan1 mesa-vulkan-drivers && \
    rm -rf /var/lib/apt/lists/*

# Bundle Umbrel-managed context in the image. Hermes enables the runtime plugin
# and external skill directory from config.yaml at startup; the files themselves
# stay outside /opt/data so the agent cannot edit managed instructions.
COPY bootstrap-umbrel-context.sh render-umbrel-context.py umbrel-setup-wrapper.py patch-umbrel-update-message.py patch-umbrel-proxy-auth.py patch-umbrel-local-memory.py /app/
RUN mkdir -p /app/umbrel-tui/dist
COPY umbrel-chat-tui-entry.js /app/umbrel-tui/dist/entry.js
COPY umbrel-context-cont-init.sh /etc/cont-init.d/50-umbrel-context
COPY umbrel-gpu-cont-init.sh /etc/cont-init.d/40-umbrel-gpu
# Skills are templates naming the Umbrel app ({{UMBREL_APP_NAME}}). Render the
# default "Hermes Agent" copy now; cont-init re-renders it for UMBREL_APP_NAME.
COPY umbrel-context/skills /app/umbrel-context-template/skills
RUN /opt/hermes/.venv/bin/python /app/render-umbrel-context.py
COPY umbrel-context/plugins/umbrel-runtime /opt/hermes/plugins/umbrel-runtime
RUN /opt/hermes/.venv/bin/python /app/patch-umbrel-update-message.py && rm /app/patch-umbrel-update-message.py
# Patch only the Umbrel image layer. Upstream Hermes still requires dashboard
# auth by default; this image can opt into trusting umbrelOS app-proxy auth.
RUN /opt/hermes/.venv/bin/python /app/patch-umbrel-proxy-auth.py && rm /app/patch-umbrel-proxy-auth.py
# Size local model windows to the memory that's free (and inside the container's
# limit), not the memory installed, so small Umbrels don't OOM on a 256K window.
RUN /opt/hermes/.venv/bin/python /app/patch-umbrel-local-memory.py && rm /app/patch-umbrel-local-memory.py
RUN chmod +x /app/bootstrap-umbrel-context.sh /app/umbrel-setup-wrapper.py /etc/cont-init.d/50-umbrel-context /etc/cont-init.d/40-umbrel-gpu

# Umbrel runs the upstream hermes user as UID/GID 1000. Bake that identity into
# the image, but keep the upstream /opt/hermes install tree root-owned and
# non-writable so v0.17+ Docker hardening remains intact.
RUN groupmod -o -g 1000 hermes && \
    usermod -u 1000 -g 1000 hermes

# Preserve the upstream /init entrypoint and command wrapper.
EXPOSE 18789
