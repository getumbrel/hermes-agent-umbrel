---
name: umbrel
description: Use when working with Hermes Agent on umbrelOS, including persistence, paths, container behavior, app updates, gateway/dashboard troubleshooting, networking, ports, or installing tools inside the app sandbox.
version: 1.0.0
---

# Umbrel Hermes Runtime

Hermes is running as an Umbrel app inside a containerized sandbox, not as a normal host Linux install. Use this skill whenever a task touches persistence, paths, installation, networking, app updates, dashboard behavior, gateway behavior, or container lifecycle.

## Filesystem

`/opt/data` is the persistent Hermes home for this Umbrel app. Put durable user files, generated artifacts, scripts, and agent-owned state there unless the user asks for another location.

Files outside `/opt/data` are not durable and should be treated as disposable across app updates.

## Containers

The Umbrel app uses two services that share `/opt/data`:

- `web`: browser terminal, dashboard process, and dashboard proxy
- `gateway`: Hermes messaging gateway

The dashboard and terminal are behind Umbrel's authenticated app proxy. Services started inside the container are not automatically reachable from umbrelOS or the public network; that exposure is controlled by the Umbrel app packaging/proxy.

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

- `localhost` is container-local; do not assume it reaches services running in another app container.
- Use the app's internal Docker service names for container-to-container traffic.
- The dashboard runs in the `web` container and is reached through the wrapper/proxy path, not by exposing a separate host port.
