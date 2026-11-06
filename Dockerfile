# Hermes Agent on Umbrel
# Thin Umbrel context layer on the official Hermes Agent image

# To update: docker buildx imagetools inspect nousresearch/hermes-agent:v0.23.0
FROM nousresearch/hermes-agent:v0.23.0@sha256:adb7890916de63245523492be8f68f9d2095fecbec4e2a6dab7960381f0fc002

USER root

# GPU acceleration for Hermes' built-in local model runtime. On Intel and AMD
# GPUs Hermes installs llama.cpp's Vulkan build, which needs the Vulkan loader
# and Mesa's drivers; without them it silently falls back to the CPU. NVIDIA's
# CUDA and Vulkan userspace comes from the host's container toolkit when
# umbrelOS grants the app the GPU permission.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libvulkan1 mesa-vulkan-drivers && \
    rm -rf /var/lib/apt/lists/*

# Voice out of the box: bake Hermes' own [voice] extra (faster-whisper, the
# "Local Whisper" speech-to-text provider), [piper] extra (Piper text to speech)
# and [tts-premium] extra (ElevenLabs' SDK, which its voices can't speak
# without) into the image's environment. Installed on demand, each lands in a
# new copy of the whole environment under /opt/data and only loads after a
# restart. Add exactly the versions Hermes' uv.lock pins, with Hermes' own uv,
# leaving everything upstream baked untouched. Then record them in the shipped
# selection like upstream's --record-selection, so later on-demand installs
# keep them. Models aren't baked: Hermes downloads the Whisper model and Piper
# voice into /opt/data on first use.
RUN set -eu; \
    uv="$(python3 -c 'import pm; print(pm.uv_launcher("uv"))')"; \
    UV_PROJECT_ENVIRONMENT=/opt/hermes/.venv "$uv" sync --frozen --inexact --no-cache \
        --compile-bytecode --no-install-project --python /opt/hermes/.venv/bin/python \
        --extra voice --extra piper --extra tts-premium; \
    python3 -c 'from pathlib import Path; from pm.build_env import _record_selection; \
        from pm.lock import Facts; from pm.paths import facts_path; \
        shipped = Facts(facts_path()).get("venv")["extras"]; \
        _record_selection(Path("/opt/hermes"), [*shipped, "voice", "piper", "tts-premium"])'; \
    chmod 0644 /opt/hermes/tools/facts.json; \
    rm -f /opt/hermes/.venv/.lock

# The screen the agent's browser runs on (Hermes's Bot Screen), so a person can
# watch it live and take over for a CAPTCHA or a login. The packages are
# Hermes's own list (tools/bot_desktop/runtime.py), without the second Chromium
# its -desktop tags add: the browser stays Hermes's pinned one. Nothing runs
# until an agent's browser opens on it, and it stops when idle. Plus emoji and
# Chinese, Japanese and Korean fonts (WenQuanYi Micro Hei: 5 MB, where Noto's
# are 93 MB), so pages, and the 🐴 Browser Use puts on the agent's tab, don't
# show empty boxes (in the agent's screenshots too).
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      tigervnc-standalone-server xfce4-panel xfwm4 xfdesktop4 xfce4-settings xfce4-terminal \
      dbus-x11 x11-xserver-utils x11-utils x11-xkb-utils xauth fonts-dejavu-core \
      fonts-noto-color-emoji fonts-wqy-microhei && \
    rm -rf /var/lib/apt/lists/*

# Bundle Umbrel-managed context in the image. Hermes enables the runtime plugin
# and external skill directory from config.yaml at startup; the files themselves
# stay outside /opt/data so the agent cannot edit managed instructions.
COPY bootstrap-umbrel-context.sh render-umbrel-context.py umbrel-setup-wrapper.py patch-umbrel-update-message.py patch-umbrel-proxy-auth.py patch-umbrel-local-memory.py patch-umbrel-export-vault-values.py /app/
RUN mkdir -p /app/umbrel-tui/dist
COPY umbrel-chat-tui-entry.js /app/umbrel-tui/dist/entry.js
COPY umbrel-context-cont-init.sh /etc/cont-init.d/50-umbrel-context
COPY umbrel-gpu-cont-init.sh /etc/cont-init.d/40-umbrel-gpu
# Skills are templates naming the Umbrel app ({{UMBREL_APP_NAME}}). Render the
# default "Hermes Agent" copy now; cont-init re-renders it for UMBREL_APP_NAME.
COPY umbrel-context/skills /app/umbrel-context-template/skills
RUN /opt/hermes/.venv/bin/python /app/render-umbrel-context.py
COPY umbrel-context/plugins/umbrel-runtime /opt/hermes/plugins/umbrel-runtime
# Voice for agents: Hermes's `murmur` speech providers, which use one pair of
# models per language (English and Chinese, downloaded into /opt/data on first
# use, pinned by sha256) and the baked-in Whisper and Piper until then and in
# every other language; and Murmur's voice calls on an agent's ChatGPT plan, which
# keep the ChatGPT sign-in inside Hermes. A bundled backend plugin, so it loads
# for every agent by itself (see its README section).
COPY umbrel-context/plugins/murmur-voice /opt/hermes/plugins/murmur-voice
# Email for agents, through an account's app password: a small plugin with the
# agent's search, read, draft and send tools (sending waits for the person's
# approval) and the routes Murmur uses to check an account. The account lives
# in each agent's own .env; the bootstrap enables the plugin for every agent,
# and its tools stay out of sight until an agent has an account (see its mail.py).
COPY umbrel-context/plugins/murmur-mail /opt/hermes/plugins/murmur-mail
# Lets an agent ask the person to take over its browser for a step only they
# can do, and wait until they hand it back, and opens the browser maximized on
# the screen (headless off it). Murmur switches it on for an agent along with
# its browser's screen, and adds it to a Hermes without it.
COPY umbrel-context/plugins/murmur-screen /opt/hermes/plugins/murmur-screen
RUN /opt/hermes/.venv/bin/python /app/patch-umbrel-update-message.py && rm /app/patch-umbrel-update-message.py
# Patch only the Umbrel image layer. Upstream Hermes still requires dashboard
# auth by default; this image can opt into trusting umbrelOS app-proxy auth.
RUN /opt/hermes/.venv/bin/python /app/patch-umbrel-proxy-auth.py && rm /app/patch-umbrel-proxy-auth.py
# Size local model windows to the memory that's free (and inside the container's
# limit), not the memory installed, so small Umbrels don't OOM on a 256K window.
RUN /opt/hermes/.venv/bin/python /app/patch-umbrel-local-memory.py && rm /app/patch-umbrel-local-memory.py
# Keep an agent's export as it is after its browser filled a saved card: the card's values are kept
# from the model, and that scrub ran over the exported files too ("hue": 120 lost its "12").
# Drop it once upstream fixes https://github.com/NousResearch/hermes-agent/issues/149596.
RUN /opt/hermes/.venv/bin/python /app/patch-umbrel-export-vault-values.py && rm /app/patch-umbrel-export-vault-values.py
RUN chmod +x /app/bootstrap-umbrel-context.sh /app/umbrel-setup-wrapper.py /etc/cont-init.d/50-umbrel-context /etc/cont-init.d/40-umbrel-gpu

# Umbrel runs the upstream hermes user as UID/GID 1000. Bake that identity into
# the image, but keep the upstream /opt/hermes install tree root-owned and
# non-writable so v0.17+ Docker hardening remains intact.
RUN groupmod -o -g 1000 hermes && \
    usermod -u 1000 -g 1000 hermes

# Preserve the upstream /init entrypoint and command wrapper.
EXPOSE 18789
