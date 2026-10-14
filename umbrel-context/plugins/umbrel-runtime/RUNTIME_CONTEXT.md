# Umbrel Runtime Context

Hermes is running as an Umbrel app inside a containerized sandbox.

- `/opt/data` is the persistent Hermes home. Put durable files, scripts, notes, generated artifacts, and agent-owned state there unless the user asks for another path.
- Files outside `/opt/data` are not durable and should be treated as disposable across app updates.
- Do not run `hermes update` or self-update Hermes. Hermes versions are managed by umbrelOS app updates and pinned Docker images.
- This app runs the official dashboard and messaging gateway in one container under upstream s6 supervision.
- The dashboard is behind the Umbrel app proxy and requires an Umbrel login by default. Other services in the container are not exposed.
- To manage umbrelOS or other Umbrel apps, use the `umbrel` MCP tools; if they aren't connected, ask the user to connect them.
- Use the `umbrel` skill for Umbrel paths, persistence, Docker/container behavior, app updates, networking, Umbrel login, Umbrel MCP, gateway issues, or troubleshooting.
