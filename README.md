# hermes-agent-umbrel

> [Official Hermes dashboard](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard) and messaging gateway for running [Hermes Agent](https://hermes-agent.nousresearch.com) on umbrelOS.

⚠️ WARNING: Running this on systems other than umbrelOS is likely very insecure. This configuration is only secure when running behind the umbrelOS app proxy.

## What is this?

This repo builds the Umbrel image layer for Hermes Agent. It does not fork Hermes itself — it adds Umbrel runtime context on top of the [official Hermes Agent Docker image](https://github.com/NousResearch/hermes-agent) and uses upstream's dashboard, chat, gateway, and s6 supervision. It provides:

- The official Hermes web dashboard, including the built-in chat tab
- First-run setup from the Chat tab when no usable model provider is configured
- The official Hermes messaging gateway for platform integrations
- Upstream s6 supervision for the dashboard and gateway inside one container
- Sandboxing so Hermes runs in its own environment that can't mess up other Umbrel apps
- Umbrel-specific runtime context and an `umbrel` skill, wired through Hermes' supported plugin/skills surfaces so the agent understands its containerized Umbrel environment

## Architecture

A single container uses upstream's `/init` entrypoint and s6 service tree:

- **dashboard** — Official Hermes dashboard, enabled with `HERMES_DASHBOARD=1` and served behind the umbrelOS app proxy.
- **gateway** — Official Hermes gateway, started through `hermes gateway run` and redirected by upstream into s6 supervision.

Both services share `/opt/data`, the persistent Hermes home. Dashboard actions such as gateway restart run inside the same s6 container and control the supervised gateway directly.

The Chat tab uses upstream's TUI with a small Umbrel launch shim. The shim checks for a usable runtime provider before chat starts; on a fresh install it runs `hermes setup` in-place, then hands off to the official TUI.

## Umbrel context

The image includes a bundled `umbrel-runtime` plugin under `/opt/hermes/plugins` and a managed `umbrel` skill under `/app/umbrel-context/skills`. Both are app-owned image content, so they update with normal umbrelOS app image updates without writing managed prompt files into the user's persistent data directory.

On startup, `/etc/cont-init.d/50-umbrel-context` runs `/app/bootstrap-umbrel-context.sh` idempotently:

- Enables the bundled `umbrel-runtime` plugin in `/opt/data/config.yaml`, so Hermes injects compact Umbrel runtime context before LLM calls through the official `pre_llm_call` hook.
- Adds the managed skills directory to `skills.external_dirs` in `/opt/data/config.yaml` when the config exists.

## License

MIT
