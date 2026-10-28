"""What Murmur asks of an agent's email account, per agent (``?profile=``):

  GET  /api/plugins/murmur-mail/status   whether the agent has email set up, and with what (never the password)
  POST /api/plugins/murmur-mail/check    sign in to both servers and bring back the three newest messages

Murmur saves the account itself, key by key, with Hermes's ``PUT /api/env`` (see ../mail.py for
the keys), so this only reads it, from the requested agent's own secret scope. A check takes the saved account,
or the one in its body: with ``address`` the body is the whole account, without it the body's
fields (say, a new ``password``) go over the saved ones. A check's failure is an answer, not an
error: ``{"ok": false, "error": {"kind", "where", "detail"}}`` with ``kind`` one of incomplete,
wrong_password, app_password, unreachable, tls or unknown. The password never comes back.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Optional, Union

from fastapi import APIRouter
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter()


def _core():
    name = "murmur_mail_dashboard_core"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent.parent / "mail.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


mail = _core()

# One check at a time per agent: each is a real sign-in, and providers lock out bursts of them.
_locks: dict[str, asyncio.Lock] = {}
CHECK_SECONDS = 60


def _home() -> str:
    try:
        from hermes_constants import get_hermes_home

        return str(get_hermes_home())
    except Exception:
        return ""


def _enabled() -> bool:
    """Whether this agent has the plugin switched on (its tools need that)."""
    try:
        from hermes_cli.config import load_config_readonly

        plugins = (load_config_readonly() or {}).get("plugins") or {}
    except Exception:
        return False
    enabled = plugins.get("enabled") if isinstance(plugins, dict) else None
    disabled = plugins.get("disabled") if isinstance(plugins, dict) else None
    on = isinstance(enabled, list) and "murmur-mail" in enabled
    off = isinstance(disabled, list) and "murmur-mail" in disabled
    return on and not off


def _saved() -> tuple[dict[str, str], bool]:
    """This agent's saved settings, and whether they're a copy of another agent's."""
    values = mail.saved_values(mail.profile_secrets())
    if mail.is_inherited(values, mail.current_agent()):
        return {}, True
    return values, False


@router.get("/status")
async def status():
    values, inherited = _saved()
    account, missing = mail.account_from(values)
    return {
        "plugin": "murmur-mail",
        "version": mail.VERSION,
        "enabled": await asyncio.to_thread(_enabled),
        "configured": account is not None,
        "account": account.public() if account else None,
        # What's left to set, when some of it is (empty for an agent without email).
        "missing": missing if values else [],
        # Another agent's account, copied into this agent's .env when Hermes made it: unused, and
        # Murmur clears it away (its keys are the same MURMUR_MAIL_ ones).
        "inherited": inherited,
    }


class CheckRequest(BaseModel):
    address: Optional[str] = None
    password: Optional[str] = None
    username: Optional[str] = None
    name: Optional[str] = None
    provider: Optional[str] = None
    imap_host: Optional[str] = None
    imap_port: Optional[Union[int, str]] = None
    imap_security: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[Union[int, str]] = None
    smtp_security: Optional[str] = None
    save_sent: Optional[Union[bool, str]] = None

    def values(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for field, value in self.model_dump().items():
            if value is None:
                continue
            if isinstance(value, bool):
                value = "1" if value else "0"
            out[field.upper()] = str(value)
        return out


def _failed(kind: str, where: str = "", detail: str = "", **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": {"kind": kind, "where": where, "detail": detail, **extra}}


@router.post("/check")
async def check(body: Optional[CheckRequest] = None):
    given = body.values() if body else {}
    given.pop("AGENT", None)
    values = given if "ADDRESS" in given else {**_saved()[0], **given}
    account, missing = mail.account_from(values)
    if account is None:
        return _failed("incomplete", missing=missing)
    lock = _locks.setdefault(_home(), asyncio.Lock())
    async with lock:
        try:
            return await asyncio.wait_for(asyncio.to_thread(mail.check, account), timeout=CHECK_SECONDS)
        except asyncio.TimeoutError:
            return _failed("unreachable", "imap", "The server took too long to answer.")
        except mail.MailError as exc:
            return {"ok": False, "error": exc.public()}
        except Exception as exc:  # anything else is still an answer, never the password
            log.warning("murmur-mail check failed: %s", type(exc).__name__)
            return {"ok": False, "error": mail.classify(exc, "imap", account).public()}
