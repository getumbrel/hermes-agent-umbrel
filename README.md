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

The image supports `HERMES_UMBREL_APP_PROXY_AUTH=1` so the dashboard can rely on umbrelOS app-proxy authentication instead of showing Hermes' own dashboard login. Do not use this outside the Umbrel app proxy.

The Chat tab uses upstream's TUI with a small Umbrel launch shim. The shim checks for a usable runtime provider before chat starts; on a fresh install it runs `hermes setup` in-place, then hands off to the official TUI.

## Umbrel context

The image includes a bundled `umbrel-runtime` plugin under `/opt/hermes/plugins` and a managed `umbrel` skill under `/app/umbrel-context/skills`. Both are app-owned image content, so they update with normal umbrelOS app image updates without writing managed prompt files into the user's persistent data directory.

On startup, `/etc/cont-init.d/50-umbrel-context`:

- Renders the `umbrel` skill from its template (`/app/umbrel-context-template/skills`) into `/app/umbrel-context/skills` as root, filling in the Umbrel app name (see `UMBREL_APP_NAME` below). The rendered files stay root-owned, so the agent cannot edit them.
- Runs `/app/bootstrap-umbrel-context.sh` as the `hermes` user, which idempotently updates the config of every agent: the default profile (`/opt/data/config.yaml`) and every existing Hermes profile (`/opt/data/profiles/<name>/config.yaml`). For each one it:
  - Enables the bundled `umbrel-runtime` plugin, so Hermes injects compact Umbrel runtime context before LLM calls through the official `pre_llm_call` hook.
  - Adds the managed skills directory to `skills.external_dirs`.

Edits are line-based, so user comments, formatting and other plugins or skill directories are preserved, and repeated boots never add duplicates. Profiles created while the container is running pick up the Umbrel context on the next start. Dot-prefixed directories under `profiles/` and directories without a `config.yaml` are skipped; profiles are never created.

The bootstrap works with both PyYAML and Hermes' own `hermes_yaml` module. Hermes v0.22.0 dropped PyYAML, which made the bootstrap fail on every start (`ModuleNotFoundError: No module named 'yaml'`), so no agent received the Umbrel context.

### Environment variables

- `UMBREL_APP_NAME` (default `Hermes Agent`): the name of the Umbrel app this image runs as, used wherever the agent's Umbrel guidance names the app (umbrelOS Files and Terminal paths, restarting the app, Umbrel login and MCP settings). Apps that bundle this image as their built-in Hermes set their own name, for example `UMBREL_APP_NAME=Murmur`, so the agent points users to Files -> Apps -> Murmur -> data -> hermes. It is read by the skill renderer at startup and by the `umbrel-runtime` plugin on every turn.
- `HERMES_UMBREL_CONTEXT` (default `1`): set to `0` to opt out. The bootstrap then removes the `umbrel-runtime` plugin and the managed skills directory from every agent's config instead of adding them, leaving everything else untouched.

## GPU acceleration for local models

Hermes can run local models with its built-in llama.cpp runtime. On Linux it installs the CUDA build for NVIDIA GPUs and the Vulkan build for Intel and AMD GPUs. This image makes those builds work on umbrelOS:

- It ships the Vulkan loader (`libvulkan1`) and Mesa's Vulkan drivers (`mesa-vulkan-drivers`: Intel ANV, AMD RADV and others), about 15 MB to download and 75-85 MB on disk. Without them the Vulkan build finds no GPU and quietly runs on the CPU.
- `/etc/cont-init.d/40-umbrel-gpu` adds the `hermes` user to the groups that own `/dev/dri/*` and `/dev/kfd`. umbrelOS grants those groups with `group_add`, but Hermes drops to `hermes` with `s6-setuidgid`, which rebuilds the supplementary groups from `/etc/group` and would otherwise drop them.

The GPU only reaches the container when the Umbrel app requests it. Add the GPU permission to the app's `umbrel-app.yml`:

```yaml
permissions:
  - GPU
```

umbrelOS then maps `/dev/dri` (and `/dev/kfd` for ROCm) with the owning host groups, and on hosts with NVIDIA's container toolkit it reserves the NVIDIA GPUs with `NVIDIA_DRIVER_CAPABILITIES=all`. The toolkit supplies NVIDIA's driver libraries, including CUDA and Vulkan, so the image does not bundle them. Apps without the permission run local models on the CPU as before.

## License

MIT
