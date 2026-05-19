# hermes-agent-umbrel

> Web terminal and [official Hermes dashboard](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard) for running [Hermes Agent](https://hermes-agent.nousresearch.com) on umbrelOS.

⚠️ WARNING: Running this on systems other than umbrelOS is likely very insecure. This configuration is only secure when running behind the umbrelOS app proxy.

## What is this?

This repo builds the Umbrel wrapper image for Hermes Agent. It does not fork Hermes itself — it layers a terminal UI, onboarding flow, and Umbrel runtime context on top of the [official Hermes Agent Docker image](https://github.com/NousResearch/hermes-agent). It provides:

- A web-based terminal UI (xterm.js) that drops you straight into chat with Hermes
- Automatic setup detection using Hermes' own runtime provider resolver — runs the setup wizard only when no usable provider credentials are configured
- The official Hermes web dashboard, proxied through the terminal server and protected by umbrelOS authentication
- A separate gateway container using the same wrapper image but the official Hermes gateway entrypoint for messaging platform integrations
- Sandboxing so Hermes runs in its own environment that can't mess up other Umbrel apps
- Umbrel-specific runtime context and an `umbrel` skill, wired through Hermes' supported plugin/skills surfaces so the agent understands its containerized Umbrel environment

## Architecture

Two containers share a single data volume (`/opt/data`):

- **web** — Node.js server providing the terminal UI (xterm.js + PTY) and the official Hermes dashboard (running as a background process, proxied via localhost). This is the main entry point behind the umbrelOS app proxy.
- **gateway** — Same wrapper image, running the official Hermes Docker entrypoint with `gateway run`. This keeps the managed Umbrel plugin/skill files available in both containers while leaving Hermes itself pinned to the official upstream image.

The terminal is served at `/terminal` (the umbrelOS app entry point). The dashboard owns `/` and all other paths are proxied to it, ensuring it's protected by umbrelOS authentication.

## Umbrel context

The image includes a bundled `umbrel-runtime` plugin under `/opt/hermes/plugins` and a managed `umbrel` skill under `/app/umbrel-context/skills`. Both are app-owned image content, so they update with normal umbrelOS app image updates without writing managed prompt files into the user's persistent data directory.

On startup, `/app/bootstrap-umbrel-context.sh` idempotently:

- Enables the bundled `umbrel-runtime` plugin in `/opt/data/config.yaml`, so Hermes injects compact Umbrel runtime context before LLM calls through the official `pre_llm_call` hook.
- Adds the managed skills directory to `skills.external_dirs` in `/opt/data/config.yaml` when the config exists.

## License

MIT
