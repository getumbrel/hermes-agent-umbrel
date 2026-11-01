"""What the live voice answers by itself never reaches Hermes: small talk stays between the person
and OpenAI. Murmur has the words of both sides, and this files them in the agent's chat as an
ordinary exchange, so the chat is whole and the agent knows what was said.

Done the way Hermes files its own model-switch note (``_append_model_switch_marker``): into the live
session and its stored rows together, so the next turn sees the exchange and doesn't save it again.
"""

from __future__ import annotations

import contextlib
from typing import Any


class Busy(Exception):
    """A turn is running, and Hermes treats any change to the history under one as a desync."""


def keep(session_key: str, you: str, voice: str) -> list[int]:
    """Adds what you said and what the voice said to this chat. Their stored row ids."""
    import tui_gateway.server as gateway
    from agent.context_compressor import _DB_PERSISTED_MARKER
    from agent.message_metadata import stamp_message_uid

    session = gateway._session_for_key(session_key)
    if session is None:
        raise LookupError(session_key)
    if session.get("running"):
        raise Busy(session_key)
    # (A chat's row is made on its first activity, and this may be it, whether or not its agent is up.)
    if gateway._ensure_session_db_row(session) is False:
        raise RuntimeError("Hermes has nowhere to save this chat")
    agent = session.get("agent")
    db = getattr(agent, "_session_db", None) if agent is not None else None
    entries = [{"role": "user", "content": you}, {"role": "assistant", "content": voice}]
    with contextlib.nullcontext(db) if db is not None else gateway._session_db(session) as store:
        if store is None:
            raise RuntimeError("Hermes has nowhere to save this chat")
        target = gateway._submit_row_target_key(session)
        for entry in entries:
            entry["_row_id"] = store.append_message(session_id=target, role=entry["role"], content=entry["content"],
                                                    message_uid=stamp_message_uid(entry))
            entry[_DB_PERSISTED_MARKER] = True
    with session["history_lock"]:
        session.setdefault("history", []).extend(entries)
        session["history_version"] = int(session.get("history_version", 0)) + 1
    return [entry["_row_id"] for entry in entries]


MAX_OPENING = 128  # messages, and
MAX_OPENING_CHARS = 32_000  # about the 8,192 tokens a live session starts with at most
_TEXT_TYPES = {"developer": "input_text", "user": "input_text", "assistant": "output_text"}


def opening(items: Any) -> list[dict]:
    """What Murmur sent of a chat, as a live session's opening messages: text only, in exactly
    the shape it takes, keeping the newest if there's too much."""
    kept: list[dict] = []
    room = MAX_OPENING_CHARS
    for item in reversed(items[-MAX_OPENING:] if isinstance(items, list) else []):
        role = item.get("role") if isinstance(item, dict) else None
        parts = item.get("content") if role in _TEXT_TYPES else None
        text = parts[0].get("text") if isinstance(parts, list) and parts and isinstance(parts[0], dict) else None
        if not isinstance(text, str) or not text.strip():
            continue
        room -= len(text)
        if room < 0:
            break
        kept.append({"type": "message", "role": role, "content": [{"type": _TEXT_TYPES[role], "text": text}]})
    return kept[::-1]
