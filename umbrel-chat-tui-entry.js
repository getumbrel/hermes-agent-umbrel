#!/usr/bin/env node

const { spawnSync } = require("node:child_process");

const hermesPython = process.env.HERMES_PYTHON || "/opt/hermes/.venv/bin/python";
const upstreamEntry = process.env.HERMES_UPSTREAM_TUI_ENTRY || "/opt/hermes/ui-tui/dist/entry.js";

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    env: { ...process.env, ...(options.env || {}) },
    stdio: options.stdio || "inherit",
    timeout: options.timeout,
  });
}

function bootstrapContext() {
  run("/app/bootstrap-umbrel-context.sh", []);
}

function providerConfigured() {
  const probe = `
from dotenv import load_dotenv
from hermes_cli.auth import has_usable_secret
from hermes_cli.config import get_env_path
from hermes_cli.main import _has_any_provider_configured
from hermes_cli.runtime_provider import resolve_runtime_provider

load_dotenv(get_env_path())
runtime = resolve_runtime_provider(requested=None)
provider_configured = bool(_has_any_provider_configured())
provider = runtime.get("provider") or "provider"
source = str(runtime.get("source") or "")

if not provider_configured and provider == "bedrock" and source in {"iam-role", "aws-sdk-default-chain"}:
    raise RuntimeError("No Hermes provider is configured.")

api_key = runtime.get("api_key")
api_key_text = "" if callable(api_key) else str(api_key or "").strip()
credential_ok = (
    callable(api_key)
    or api_key_text in {"aws-sdk", "no-key-required"}
    or has_usable_secret(api_key_text)
    or bool(runtime.get("command"))
)

if not credential_ok:
    raise RuntimeError(f"No usable credentials found for {provider}.")
`;
  const timeout = Number.parseInt(process.env.UMBREL_PROVIDER_CHECK_TIMEOUT_MS || "5000", 10);
  const result = run(hermesPython, ["-c", probe], {
    env: {
      HERMES_NOUS_TIMEOUT_SECONDS: process.env.HERMES_NOUS_TIMEOUT_SECONDS || "2",
    },
    stdio: "ignore",
    timeout,
  });
  return result.status === 0;
}

function exitStatus(result, fallback = 1) {
  if (typeof result.status === "number") return result.status;
  if (result.signal) return 128;
  return fallback;
}

bootstrapContext();

if (!providerConfigured()) {
  process.stdout.write("\nHermes needs a model provider before chat can start.\n");
  process.stdout.write("Starting setup now. You can press Ctrl+C to cancel.\n\n");

  const setup = run(hermesPython, ["/app/umbrel-setup-wrapper.py"]);
  bootstrapContext();

  const setupStatus = exitStatus(setup);
  if (setupStatus !== 0 || !providerConfigured()) {
    process.stdout.write("\nSetup is not complete yet. Restart Hermes Agent or open Chat again to retry.\n");
    process.exit(setupStatus === 0 ? 1 : setupStatus);
  }
}

const tui = run(process.execPath, ["--expose-gc", upstreamEntry, ...process.argv.slice(2)]);
process.exit(exitStatus(tui, 0));
