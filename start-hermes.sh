#!/bin/bash
# Launches hermes setup if no provider is configured, then starts chat.
# After hermes exits, drops to a shell so users can run admin commands
# like `hermes model`, `hermes doctor`, etc.
#
# Detection uses hermes's own resolve_provider() which checks OAuth auth
# store, env vars, and the full provider registry. We load .env first
# because hermes reads API keys from the environment, not the file directly.
# If this breaks after a hermes update, check whether resolve_provider was
# renamed or moved — it lives in hermes_cli/auth.py.

if ! python3 -c "from dotenv import load_dotenv; load_dotenv('/opt/data/.env'); from hermes_cli.auth import resolve_provider; resolve_provider()" 2>/dev/null; then
  hermes setup
fi

hermes

exec bash
