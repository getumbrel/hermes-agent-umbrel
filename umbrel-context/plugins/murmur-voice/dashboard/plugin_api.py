"""Murmur's voices: local models for an agent's language, and voice calls on a ChatGPT plan.

Local voices (../core). Hermes's ``murmur`` speech providers use the language's pair of models once
it's downloaded, and Piper and Whisper until then, and in every other language. These routes
show Murmur that pair and fetch it:

  GET  /api/plugins/murmur-voice/local?profile=&language=    the pair, what's left to download,
                                                             what runs now, the download
  POST /api/plugins/murmur-voice/local/install?profile=      {language?} -> the same

Installing only fetches; Murmur switches the agent over itself (``tts.provider`` / ``stt.provider:
murmur``), and the providers pick the new models up the moment they're ready.

Calls on an agent's ChatGPT plan. Murmur (a web app for Hermes) can hold a live voice call with an
agent using the ChatGPT sign-in the agent already has, the way OpenAI's Codex CLI talks on
a ChatGPT plan: OpenAI's live voice model listens and speaks, and hands every
real request to Hermes ("client delegation"). This is the one part that has to
run inside Hermes. It holds the sign-in, swaps the browser's WebRTC offer for
OpenAI's answer, and keeps the call's control channel, so the token never
leaves Hermes. Audio goes straight between the browser and OpenAI; the browser
turns each request into an ordinary Hermes voice turn and sends back what to
say.

  POST /api/plugins/murmur-voice/say?profile=            {text} -> speech as MP4 fragments, as it's
                                                         made: what an iPhone plays a call through
  WS   /api/plugins/murmur-voice/hear?profile=           a call's mic as an iPhone records it in
                                                         (with {mode}, {interrupted}, {finish});
                                                         {type: speech|nothing}, and each finished
                                                         turn as a WAV, out
  POST /api/plugins/murmur-voice/spoken?profile=         {chat, you, voice} -> {rows}: an exchange
                                                         the live voice answered by itself
  GET  /api/plugins/murmur-voice/chatgpt?profile=        {available, reason?}
  POST /api/plugins/murmur-voice/chatgpt/call?profile=   {sdp, instructions?, voice?, history?} -> {call, sdp}
  WS   /api/plugins/murmur-voice/chatgpt/call/{call}     the call's events in; say, note, close out

The call route (chatgpt.com/backend-api/codex/realtime/calls) is the one Codex
uses with a ChatGPT sign-in. It isn't part of OpenAI's published API and may
change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

import importlib.util
import sys
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter()

CALLS_URL = "https://chatgpt.com/backend-api/codex/realtime/calls?intent=quicksilver&architecture=avas"
CONTROL_URL = "wss://api.openai.com/v1/live/{call}"
MODEL = "gpt-live-1-codex"
MAX_SPOKEN = 20_000
MAX_SAID = 2_000
VOICES = ("arbor", "breeze", "cove", "ember", "juniper", "maple", "sol", "spruce", "vale")
DEFAULT_VOICE = "cove"
# Each piece of context the voice takes is at most this many bytes.
MAX_APPEND_BYTES = 500
MAX_SDP_BYTES = 64 * 1024
MAX_INSTRUCTIONS = 8000
# A call nobody attached to is hung up after this long.
ATTACH_SECONDS = 30
CALL_ID = re.compile(r"^(?:rtc_[A-Za-z0-9_-]{1,128}|[0-9a-fA-F-]{36})$")
# Audio flows over WebRTC; the control channel's copies of it (both ways) aren't worth relaying.
def _is_audio(kind: object) -> bool:
    return isinstance(kind, str) and "audio" in kind and kind.endswith((".delta", ".append"))


@dataclass
class Call:
    id: str
    headers: dict[str, str]
    events: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=2000))
    control: Any = None
    attached: bool = False
    done: asyncio.Event = field(default_factory=asyncio.Event)


_calls: dict[str, Call] = {}
# One connection to chatgpt.com, kept open between calls: a fresh one costs a TLS handshake.
_client: Optional[httpx.AsyncClient] = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=30, http2=False)
    return _client


def _credentials(*, read_only: bool) -> Optional[str]:
    """The agent's ChatGPT access token (refreshed when it's close to expiring), or None."""
    from hermes_cli.auth_codex import resolve_codex_runtime_credentials

    try:
        creds = resolve_codex_runtime_credentials(read_only=read_only, refresh_skew_seconds=900)
    except Exception:
        return None
    token = creds.get("api_key") if isinstance(creds, dict) else None
    return token if isinstance(token, str) and token.strip() else None


def _headers(token: str) -> dict[str, str]:
    # Hermes's own identity and the account headers it sends to chatgpt.com.
    from agent.codex_headers import codex_cloudflare_headers

    headers = codex_cloudflare_headers(token)
    headers.update(
        {
            "Authorization": f"Bearer {token}",
            "OpenAI-Alpha": "quicksilver=v2",
            "session-id": str(uuid.uuid4()),
            "thread-id": str(uuid.uuid4()),
            "x-session-id": str(uuid.uuid4()),
        }
    )
    return headers


def _call_id(response: httpx.Response) -> str:
    location = response.headers.get("location", "")
    if location:
        parsed = urlparse(location)
        if parsed.netloc and parsed.netloc not in {"api.openai.com", "chatgpt.com"}:
            raise HTTPException(status_code=502, detail="ChatGPT answered with an unexpected call address")
        for part in reversed(parsed.path.split("/")):
            if CALL_ID.fullmatch(part):
                return part
    fallback = response.headers.get("openai-session-id", "")
    if CALL_ID.fullmatch(fallback):
        return fallback
    raise HTTPException(status_code=502, detail="ChatGPT didn't say which call it started")


def _chunks(text: str) -> list[str]:
    """Text in pieces of at most MAX_APPEND_BYTES, cut between characters."""
    out, current, size = [], "", 0
    for ch in text:
        width = len(ch.encode("utf-8"))
        if size + width > MAX_APPEND_BYTES:
            out.append(current)
            current, size = "", 0
        current += ch
        size += width
    if current:
        out.append(current)
    return out


@router.get("/chatgpt")
async def status():
    token = await asyncio.to_thread(_credentials, read_only=True)
    return {"available": bool(token), **({} if token else {"reason": "not_signed_in"})}


class CallRequest(BaseModel):
    sdp: str
    instructions: Optional[str] = None
    voice: Optional[str] = None
    history: Optional[list] = None


@router.post("/chatgpt/call")
async def start_call(body: CallRequest):
    sdp = body.sdp or ""
    if len(sdp.encode("utf-8")) > MAX_SDP_BYTES or not re.match(r"^v=0\r?\n", sdp) or "m=audio " not in sdp:
        raise HTTPException(status_code=400, detail="An audio SDP offer is required")
    token = await asyncio.to_thread(_credentials, read_only=False)
    if not token:
        raise HTTPException(status_code=409, detail="This agent isn't signed in with ChatGPT")
    headers = _headers(token)
    session = {
        "model": MODEL,
        "instructions": (body.instructions or "")[:MAX_INSTRUCTIONS],
        "audio": {"output": {"voice": body.voice if body.voice in VOICES else DEFAULT_VOICE}},
        "delegation": {"type": "client"},
    }
    # (`initial_items` here, what OpenAI's public API calls `input`. That one ends the call.)
    opening = local_chat.opening(body.history)
    if opening:
        session["initial_items"] = opening
    try:
        response = await _http().post(CALLS_URL, json={"sdp": sdp, "session": session}, headers=headers, follow_redirects=False)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Couldn't reach ChatGPT") from exc
    if response.status_code not in (200, 201):
        # Never relay ChatGPT's own text: it can echo account details.
        log.warning("ChatGPT voice call refused: HTTP %s", response.status_code)
        raise HTTPException(status_code=502, detail=f"ChatGPT didn't start the call (HTTP {response.status_code})")
    answer = response.text
    if token in answer or not answer.startswith("v=0"):
        raise HTTPException(status_code=502, detail="ChatGPT answered with something that isn't a call")
    call = Call(id=_call_id(response), headers=headers)
    key = secrets.token_urlsafe(18)
    _calls[key] = call
    asyncio.get_running_loop().create_task(_control(key, call))
    return {"call": key, "sdp": answer}


async def _control(key: str, call: Call) -> None:
    """The call's control channel: OpenAI's events into the queue until the call ends."""
    from websockets.asyncio.client import connect

    try:
        async with connect(CONTROL_URL.format(call=call.id), additional_headers=call.headers, max_size=4 * 1024 * 1024) as ws:
            call.control = ws
            waiting = asyncio.get_running_loop().create_task(_hang_up_unattended(call))
            try:
                async for raw in ws:
                    try:
                        event = json.loads(raw)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(event, dict) or _is_audio(event.get("type")):
                        continue
                    if event.get("type") == "error":
                        # Only its code: the message can echo account details.
                        error = event.get("error") if isinstance(event.get("error"), dict) else {}
                        event = {"type": "error", "code": error.get("code") or event.get("code") or "error"}
                    if call.events.full():
                        call.events.get_nowait()
                    call.events.put_nowait(event)
            finally:
                waiting.cancel()
    except Exception as exc:  # the browser hears the call ended either way
        log.warning("ChatGPT voice control channel closed: %s", type(exc).__name__)
    finally:
        call.control = None
        call.events.put_nowait({"type": "session.closed", "reason": "ended"})
        call.done.set()
        _calls.pop(key, None)


async def _hang_up_unattended(call: Call) -> None:
    await asyncio.sleep(ATTACH_SECONDS)
    if not call.attached and call.control is not None:
        await call.control.close()


def _authorized(ws: WebSocket) -> bool:
    try:
        from hermes_cli import web_server_chat
    except Exception:
        return False
    return bool(web_server_chat._ws_auth_ok(ws))


@router.websocket("/chatgpt/call/{key}")
async def attach(ws: WebSocket, key: str):
    call = _calls.get(key)
    if not _authorized(ws) or call is None or call.attached:
        await ws.close(code=4404)
        return
    call.attached = True
    await ws.accept()

    async def relay_out():
        while True:
            event = await call.events.get()
            await ws.send_text(json.dumps(event))
            if event.get("type") == "session.closed":
                return

    sender = asyncio.get_running_loop().create_task(relay_out())
    try:
        while not sender.done():
            receiving = asyncio.ensure_future(ws.receive_text())
            finished, _ = await asyncio.wait({receiving, sender}, return_when=asyncio.FIRST_COMPLETED)
            if receiving not in finished:
                receiving.cancel()
                break
            await _command(call, receiving.result())
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        if call.control is not None:
            try:
                await call.control.send(json.dumps({"type": "session.close"}))
                await asyncio.wait_for(call.done.wait(), timeout=5)
            except Exception:
                await call.control.close()
        try:
            await ws.close()
        except Exception:
            pass


async def _command(call: Call, raw: str) -> None:
    """What Murmur asks of the voice: say this, or know this (quietly), or hang up."""
    try:
        command = json.loads(raw)
    except ValueError:
        return
    if not isinstance(command, dict) or call.control is None:
        return
    kind = command.get("type")
    text = command.get("text") if isinstance(command.get("text"), str) else ""
    delegation = command.get("delegation") if isinstance(command.get("delegation"), str) else None
    if kind == "close":
        await call.control.send(json.dumps({"type": "session.close"}))
        return
    if kind not in {"say", "note"} or not text.strip():
        return
    # Said out loud, or kept as context for what it says next.
    channel = "speakable" if kind == "say" else "commentary"
    for piece in _chunks(text):
        content = [{"type": "input_text", "text": piece}]
        if delegation:
            message = {"type": "delegation.context.append", "delegation_item_id": delegation, "channel": channel, "content": content}
        else:
            message = {"type": "session.context.append", "channel": channel, "content": content}
        await call.control.send(json.dumps(message))


# ── Local voices ────────────────────────────────────────────────────────────────────────────────

def _core():
    """core/ under the one name Hermes's speech providers load it as (../__init__.py)."""
    name = "murmur_voice_core"
    if name in sys.modules:
        return sys.modules[name]
    folder = Path(__file__).resolve().parent.parent / "core"
    spec = importlib.util.spec_from_file_location(name, folder / "__init__.py", submodule_search_locations=[str(folder)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


_core()
from murmur_voice_core import catalog as local_catalog, chat as local_chat, encode as local_encode, hear as local_hear, speech as local_speech, store as local_store  # noqa: E402


@router.get("/local")
async def local_status(language: Optional[str] = None):
    return await asyncio.to_thread(local_speech.status, language)


class InstallRequest(BaseModel):
    language: Optional[str] = None


@router.post("/local/install")
async def local_install(body: InstallRequest):
    def run():
        language = local_catalog.language_of(body.language) if body.language else local_speech.settings()["language"]
        local_store.install([local_speech.wanted(kind, language) for kind in ("speak", "listen")])
        return local_speech.status(language)

    return await asyncio.to_thread(run)


@router.websocket("/hear")
async def hear(ws: WebSocket):
    """A call's mic, as an iPhone records it, and when each turn ends (core/hear.py)."""
    if not _authorized(ws):
        await ws.close(code=4403)
        return
    await ws.accept()
    loop = asyncio.get_running_loop()
    heard: asyncio.Queue = asyncio.Queue()
    ear = local_hear.Ear(lambda item: loop.call_soon_threadsafe(heard.put_nowait, item))

    async def relay():
        while True:
            item = await heard.get()
            if item is None:  # the phone falls back on the level
                await ws.close()
                return
            await (ws.send_bytes(item) if isinstance(item, bytes) else ws.send_text(json.dumps(item)))

    sender = loop.create_task(relay())
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                ear.feed(message["bytes"])
            elif message.get("text"):
                try:
                    command = json.loads(message["text"])
                except ValueError:
                    continue
                if isinstance(command, dict):
                    ear.feed(command)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        sender.cancel()
        ear.close()


class SpokenRequest(BaseModel):
    chat: str
    you: str
    voice: str


@router.post("/spoken")
async def keep_spoken(body: SpokenRequest):
    """An exchange the live voice answered by itself, into the agent's chat as ordinary messages."""
    you, voice = body.you.strip(), body.voice.strip()
    if not you or not voice or len(you) + len(voice) > MAX_SPOKEN:
        raise HTTPException(status_code=400, detail="Both sides of the exchange are needed")
    try:
        return {"rows": await asyncio.to_thread(local_chat.keep, body.chat, you, voice)}
    except LookupError:
        raise HTTPException(status_code=404, detail="This chat isn't open") from None
    except local_chat.Busy:
        raise HTTPException(status_code=409, detail="The agent is working in this chat") from None


class SayRequest(BaseModel):
    text: str


async def _spoken(text: str, language: str):
    """A sentence as MP4 fragments. It's made in a thread of its own, told the moment the listener
    goes: a generator left waiting for a reader that's been talked over would keep the voice to
    itself, and the answer to whatever interrupted it would wait for it in silence."""
    loop = asyncio.get_running_loop()
    pieces: asyncio.Queue = asyncio.Queue()
    gone = threading.Event()

    def make():
        speech = local_encode.fragments(local_speech.Speaker().stream(text, language=language, gone=gone))
        try:
            for piece in speech:
                if gone.is_set():
                    return
                loop.call_soon_threadsafe(pieces.put_nowait, piece)
        except Exception:
            log.exception("murmur-voice: couldn't say that")
        finally:
            speech.close()
            loop.call_soon_threadsafe(pieces.put_nowait, None)

    loop.run_in_executor(None, make)
    try:
        while (piece := await pieces.get()) is not None:
            yield piece
    finally:
        gone.set()


@router.post("/say")
async def say(body: SayRequest):
    """A sentence in this agent's voice, sent as it's made, for an audio element to play."""
    text = body.text.strip()
    if not text or len(text) > MAX_SAID:
        raise HTTPException(status_code=400, detail="A sentence to say is needed")
    language = (await asyncio.to_thread(local_speech.settings))["language"]
    return StreamingResponse(_spoken(text, language), media_type="audio/mp4", headers={"Cache-Control": "no-store"})
