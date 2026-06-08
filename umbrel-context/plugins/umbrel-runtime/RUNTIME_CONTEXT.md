# Umbrel Runtime Context

Hermes is running as an Umbrel app inside a containerized sandbox.

- `/opt/data` is the persistent Hermes home. Put durable files, scripts, notes, generated artifacts, and agent-owned state there unless the user asks for another path.
- Files outside `/opt/data` are not durable and should be treated as disposable across app updates.
- Do not run `hermes update` or self-update Hermes. Hermes versions are managed by umbrelOS app updates and pinned Docker images.
- This app runs the official dashboard and messaging gateway in one container under upstream s6 supervision.
- The dashboard is behind the Umbrel app proxy. Services started inside the container are not automatically reachable from umbrelOS or the public network; that exposure is controlled by the Umbrel app packaging/proxy.
- Use the `umbrel` skill for Umbrel paths, persistence, Docker/container behavior, app updates, networking, gateway issues, or troubleshooting.
