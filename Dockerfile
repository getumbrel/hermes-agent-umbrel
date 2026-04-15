# Hermes Agent on Umbrel
# Web terminal wrapper around the official Hermes Agent image

FROM nousresearch/hermes-agent:v2026.4.13

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

# Ensure hermes venv is in PATH (base image sets this in its entrypoint, not ENV)
ENV PATH="/opt/hermes/.venv/bin:${PATH}"

# The dashboard's first-run npm build writes to /opt/hermes/web/ which is owned
# by UID 10000 in the base image. Since we run as UID 1000 (via compose user:),
# we need to fix ownership at build time.
# Dashboard needs write access to web/ (npm install), hermes_cli/web_dist/ (build
# output), and /.npm (cache). All owned by UID 10000 in the base image.
RUN mkdir -p /opt/hermes/hermes_cli/web_dist && \
    chown -R 1000:1000 /opt/hermes/web/ /opt/hermes/hermes_cli/web_dist/ /app/ && \
    mkdir -p /.npm && chown -R 1000:1000 /.npm

# UID/GID set via user: 1000:1000 in docker-compose.yml
ENTRYPOINT ["/app/entrypoint.sh"]
EXPOSE 18789
