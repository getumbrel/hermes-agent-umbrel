---
name: umbrel
description: Use when working with Hermes Agent on umbrelOS, including persistence, paths, container behavior, app updates, gateway/dashboard troubleshooting, networking, ports, Umbrel login, client apps that can't connect, managing umbrelOS or other Umbrel apps via the Umbrel MCP server, or installing tools inside the app sandbox.
version: 1.1.0
---

# Umbrel Hermes Runtime

Hermes is running as an Umbrel app inside a containerized sandbox, not as a normal host Linux install. Use this skill whenever a task touches persistence, paths, installation, networking, app updates, dashboard behavior, gateway behavior, or container lifecycle.

## Filesystem

`/opt/data` is the persistent Hermes home for this Umbrel app. Put durable user files, generated artifacts, scripts, and agent-owned state there unless the user asks for another location.

Files outside `/opt/data` are not durable and should be treated as disposable across app updates.

## Umbrel User Workflows

When giving the user terminal commands to run themselves, explain the umbrelOS path first: Settings -> Advanced Settings -> Terminal -> App -> Hermes Agent.

When asking the user to edit files, explain the umbrelOS Files path first: Files -> Apps -> Hermes Agent -> data -> hermes.

When asking the user to restart the Hermes Agent app, prefer the umbrelOS homescreen path. On desktop, right-click the Hermes Agent app icon and choose Restart. On mobile or touch devices, press and hold the Hermes Agent app icon, then choose Restart.

## Containers

The Umbrel app uses one container with upstream s6 supervision:

- official dashboard service
- official Hermes messaging gateway service

Interactive chats from the dashboard chat tab and messaging-platform chats such as Telegram run inside the same app container, sharing `/opt/data`.

The dashboard Chat tab starts with an Umbrel launch shim. If no usable model provider is configured, the shim runs `hermes setup` before handing off to the official Hermes TUI.

The dashboard is behind Umbrel's app proxy, which requires an Umbrel login by default; Hermes' own dashboard login is off because Umbrel's covers it. Users can turn it off from the umbrelOS home screen: right-click (or press and hold) the Hermes Agent icon -> Settings -> Require Umbrel login. If a client app (e.g. Hermes desktop or a mobile app) can't connect to Hermes, this login is the likely cause; suggest turning it off.

Other services started inside the container are not exposed; the app proxy only forwards to the dashboard.

## Managing umbrelOS and Other Apps

You can't reach umbrelOS or other Umbrel apps from this container. Use the `umbrel` MCP tools. If they aren't connected, ask the user to open Settings -> AI agents (MCP) in umbrelOS, turn it on, choose Hermes Agent, grant the access you need, and paste the message it shows here. Add it to `/opt/data/config.yaml`, then run `/reload-mcp`:

```yaml
mcp_servers:
  umbrel:
    url: "http://10.21.0.1/mcp"
    headers:
      Authorization: "Bearer <token>"
```

Use `http://10.21.0.1/mcp`; `umbrel.local` doesn't resolve in the container. If a tool call is denied, ask the user to grant that access in Settings -> AI agents (MCP).

## Updates

Do not run `hermes update` or install a different Hermes version from inside the app. Umbrel updates Hermes by shipping pinned Docker images through app updates.

If Hermes reports that an update is available, explain that updates are managed by umbrelOS.

## Persistence Guidelines

- Put durable scripts, generated files, notes, project work, and custom tools under `/opt/data`.
- Avoid relying on `/tmp`, `/app`, default home directories, or system package state for anything that should survive an app update.

## Package Installation

The container may not behave like a full host OS. Prefer user-space installs under `/opt/data` where practical.

When installing tools:

- Prefer language-level or single-binary installs that can live under `/opt/data`.
- Keep caches and build outputs under `/opt/data` when possible.
- Avoid changes that require systemd.
- Be explicit when an installation step writes outside `/opt/data` and may be lost on app update.

## Networking

- `localhost` is container-local; it reaches services in the Hermes app container, but not services in other Umbrel app containers.
- Use the app's internal Docker service names for container-to-container traffic.
- The dashboard listens inside the container and is reached through the Umbrel app proxy, not by exposing a separate public host port.
