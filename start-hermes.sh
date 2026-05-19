#!/bin/bash
# Terminal entrypoint: enable managed Umbrel context, run setup only when
# Hermes cannot find a configured provider, then start chat and leave users in
# a shell for follow-up admin commands.

/app/bootstrap-umbrel-context.sh

if ! python3 -c "from dotenv import load_dotenv; load_dotenv('/opt/data/.env'); from hermes_cli.auth import resolve_provider; resolve_provider()" 2>/dev/null; then
  hermes setup
fi

/app/bootstrap-umbrel-context.sh

hermes

exec bash
