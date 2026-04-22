# Hermes Agent on Umbrel
# Web terminal wrapper around the official Hermes Agent image

# To update: docker buildx imagetools inspect nousresearch/hermes-agent:v2026.4.16
FROM nousresearch/hermes-agent:v2026.4.16@sha256:14ba9a26cf2d498ea773f1825326c404795ec4cb436a9479d22b7a345396c370

USER root

# Install Node.js (for terminal web server) and build tools (for node-pty native addon)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl make g++ && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install server dependencies
COPY package.json /app/
RUN cd /app && npm install --omit=dev

# Copy server files
COPY server.cjs terminal.html logo.png entrypoint.sh start-hermes.sh /app/
RUN chmod +x /app/entrypoint.sh /app/start-hermes.sh

# Ensure hermes venv is in PATH everywhere. The agent's terminal tool spawns
# subprocesses that may not inherit Docker ENV, so we cover all cases:
# - ENV: for the entrypoint, Node server, and direct child processes
# - /etc/profile.d/: for login shells (which /etc/profile resets PATH on)
# - symlink: guarantees `hermes` is found regardless of how the shell is spawned
ENV PATH="/opt/hermes/.venv/bin:${PATH}"
RUN echo 'export PATH="/opt/hermes/.venv/bin:$PATH"' > /etc/profile.d/hermes-venv.sh && \
    ln -s /opt/hermes/.venv/bin/hermes /usr/local/bin/hermes

# The dashboard's first-run npm build writes to /opt/hermes/web/ which is owned
# by UID 10000 in the base image. Since we run as UID 1000 (via compose user:),
# we need to fix ownership at build time.
# Dashboard needs write access to web/ (npm install), hermes_cli/web_dist/ (build
# output), and /.npm (cache). All owned by UID 10000 in the base image.
RUN mkdir -p /opt/hermes/hermes_cli/web_dist && \
    chown -R 1000:1000 /opt/hermes/web/ /opt/hermes/hermes_cli/web_dist/ /app/ && \
    mkdir -p /.npm && chown -R 1000:1000 /.npm

# Add a passwd entry for UID 1000 so bash shows a name instead of
# "I have no name!@..." when dropping to a shell via Ctrl+D.
# The base image's hermes user is UID 10000 — we leave it untouched.
RUN groupadd -g 1000 umbrel && useradd -u 1000 -g 1000 -d /opt/data -s /bin/bash umbrel

# UID/GID set via user: 1000:1000 in docker-compose.yml
ENTRYPOINT ["/app/entrypoint.sh"]
EXPOSE 18789
