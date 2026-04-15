# hermes-agent-umbrel

> Web terminal and [official Hermes dashboard](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard) for running [Hermes Agent](https://hermes-agent.nousresearch.com) on umbrelOS.

⚠️ WARNING: Running this on systems other than umbrelOS is likely very insecure. This configuration is only secure when running behind the umbrelOS app proxy.

## What is this?

This repo builds the web-facing container for Hermes Agent on umbrelOS. It does not package Hermes itself — it layers a terminal UI and onboarding flow on top of the [official Hermes Agent Docker image](https://github.com/NousResearch/hermes-agent). It provides:

- A web-based terminal UI (xterm.js) that drops you straight into chat with Hermes
- Automatic setup detection — runs the setup wizard on first launch, then chat on subsequent visits
- The official Hermes web dashboard, proxied through the terminal server and protected by umbrelOS authentication
- A separate gateway container (official Hermes image, untouched) for messaging platform integrations (Telegram, Discord, WhatsApp, etc.)
- Sandboxing so Hermes runs in its own environment that can't mess up other Umbrel apps

## Architecture

Two containers share a single data volume (`/opt/data`):

- **web** — Node.js server providing the terminal UI (xterm.js + PTY) and the official Hermes dashboard (running as a background process, proxied via localhost). This is the main entry point behind the umbrelOS app proxy.
- **gateway** — Official Hermes Agent image (`nousresearch/hermes-agent`) running the messaging gateway for platform integrations. Used as-is with no modifications.

The dashboard is only accessible through the web container's proxied port, ensuring it's protected by umbrelOS authentication.

## License

MIT
