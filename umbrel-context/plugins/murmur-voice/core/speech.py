"""Hermes's ``murmur`` voice: one TTS provider and one STT provider that use the language's pair of
models (catalog.PAIRS) once it's downloaded, and Hermes's own Piper and Whisper (baked into the
image) until then. Switching over needs nobody: the first sentence after a download finishes is
already spoken by the new model.

Each sentence goes to the voice that fits what's written, so an agent set to English can say a
line of Chinese and the other way round. That second voice is fetched the first time a sentence
needs it, and Piper says those sentences until it's here.

An agent picks it with ``tts.provider: murmur`` and ``stt.provider: murmur``. ``stt.language`` is
the language of both sides. Piper speaks with ``tts.piper.voice`` and Whisper listens with
``stt.local.model`` while they stand in, and for every language without a pair.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from typing import Any, Iterator, Optional

from agent.transcription_provider import TranscriptionProvider
from agent.tts_provider import TTSProvider

from . import catalog, engines, store

log = logging.getLogger("murmur_voice")

NAME = "murmur"
# A call is on (Hermes's TTS lease is held), with that agent's settings: a model that finishes
# downloading meanwhile is loaded at once, so the first sentence it says doesn't wait for it.
_calling: Optional[dict] = None


def _config() -> dict:
    try:
        from hermes_cli.config import load_config_readonly

        return load_config_readonly() or {}
    except Exception:
        return {}


def settings(config: Optional[dict] = None) -> dict[str, Any]:
    """This agent's language, and whether each side of it uses this plugin."""
    config = _config() if config is None else config
    stt = config.get("stt") if isinstance(config.get("stt"), dict) else {}
    tts = config.get("tts") if isinstance(config.get("tts"), dict) else {}
    return {
        "language": catalog.language_of(stt.get("language")),
        "on": {"speak": str(tts.get("provider") or "") == NAME, "listen": str(stt.get("provider") or "") == NAME},
    }


def wanted(kind: str, language: str) -> catalog.Model:
    """The model for this side and language (a machine with no packages for it stays on the built-in)."""
    model = catalog.pair(language)[kind]
    return model if store.supported(model) else catalog.BUILT_IN[kind]


def running(kind: str, language: str) -> catalog.Model:
    """What runs now: the language's model once it's downloaded, Piper or Whisper until then."""
    model = wanted(kind, language)
    return model if store.ready(model) else catalog.BUILT_IN[kind]


def voice_for(text: str, language: str) -> tuple[catalog.Model, str]:
    """The voice for this sentence and the language it's in. Piper until that voice is here, which
    the first sentence to need it sends for."""
    kin = catalog.sentence_language(text, language)
    model = wanted("speak", kin)
    if store.ready(model):
        return model, kin
    if not model.builtin:
        store.install([model])
    return catalog.PIPER, kin


def status(language: Optional[str] = None, config: Optional[dict] = None) -> dict[str, Any]:
    """Everything Murmur shows about local voices for one agent (dashboard/plugin_api.py)."""
    mine = settings(config)
    language = catalog.language_of(language) if language else mine["language"]
    pair = {kind: wanted(kind, language) for kind in ("speak", "listen")}
    store.resume()
    return {
        "version": 2,
        "language": language,
        "pair": {kind: {"id": m.id, "name": m.name, "builtin": m.builtin} for kind, m in pair.items()},
        "size": sum(size for _, _, size in store.needs(list(pair.values()))),
        "on": mine["on"],
        "active": {kind: running(kind, language).id for kind in pair},
        "loaded": engines.loaded(),
        "download": store.job.snapshot(),
    }


# ── Hermes's own engines, standing in ───────────────────────────────────────────────────────────

def _piper(text: str, output_path: str, language: str) -> str:
    from tools.tts_tool import _load_tts_config
    from tools.tts_tool_local import _generate_piper_tts

    config = _load_tts_config()
    voice = catalog.PIPER_VOICES.get(language)
    if voice:
        config = {**config, "piper": {**(config.get("piper") or {}), "voice": voice}}
    return _generate_piper_tts(text, output_path, config)


def _whisper(file_path: str) -> dict[str, Any]:
    from tools.transcription_tools import transcribe_audio_local_fallback

    result = transcribe_audio_local_fallback(file_path)
    return {**result, "provider": NAME} if isinstance(result, dict) else result


def _warm_stand_in(kind: str) -> None:
    """Whisper or Piper, loaded (and downloaded, the first time) ahead of the first turn, the way
    Hermes's own leases warm them when they're the agent's provider."""
    if kind == "listen":
        from tools.stt_lease import warm_stt_provider

        warm_stt_provider(provider="local")
    else:
        from tools.tts_tool import _load_tts_config
        from tools.tts_tool_local import _load_piper_voice_for_config

        _load_piper_voice_for_config(_load_tts_config())


def _mouths(language: str) -> list[catalog.Model]:
    """The voices a call in this language may use that are here, its own first."""
    voices = [running("speak", language)]
    if language in catalog.PAIRS:
        voices += [running("speak", other) for other in catalog.PAIRS if other != language]
    return list(dict.fromkeys(m for m in voices if not m.builtin))


def _preload() -> None:
    mine = _calling
    if not mine:
        return
    if mine["on"]["speak"]:
        for model in _mouths(mine["language"]):
            engines.get(model)
    if mine["on"]["listen"] and not (model := running("listen", mine["language"])).builtin:
        engines.get(model)


# (In a thread of its own: loading takes seconds, and the next download shouldn't wait for it.)
store.on_installed.append(lambda: threading.Thread(target=_preload, name="murmur-voice-preload", daemon=True).start())


class Speaker(TTSProvider):
    """Speaks each sentence with Pocket TTS or Matcha (Piper until they're downloaded, and in other
    languages)."""

    @property
    def name(self) -> str:
        return NAME

    @property
    def display_name(self) -> str:
        return "Murmur local voice"

    def get_setup_schema(self) -> dict[str, Any]:
        return {"name": self.display_name, "badge": "local", "tag": "Pocket TTS on this machine, Piper until it's downloaded",
                "env_vars": []}

    def is_available(self) -> bool:
        return True  # Piper ships with the image

    @property
    def streams_pcm(self) -> bool:
        try:
            return bool(_mouths(settings()["language"]))
        except Exception:
            return False

    @property
    def stream_sample_rate(self) -> Optional[int]:
        return engines.STREAM_RATE

    def synthesize(self, text: str, output_path: str, *, voice: Optional[str] = None, model: Optional[str] = None,
                   speed: Optional[float] = None, format: str = "wav", **extra: Any) -> str:
        now, kin = voice_for(text, settings()["language"])
        started = time.monotonic()
        written = None
        if not now.builtin:
            try:
                audio = engines.get(now).wav(text)
                written = os.path.splitext(output_path)[0] + ".wav"
                with open(written, "wb") as f:
                    f.write(audio)
            except Exception:
                # A model that won't load or run doesn't leave the agent mute: Piper says it.
                log.exception("murmur-voice: %s couldn't speak; Piper stands in", now.id)
                now = catalog.PIPER
        if written is None:
            store.resume()
            written = _piper(text, output_path, kin)
        log.info("murmur-voice: spoke with %s in %.2fs", now.id, time.monotonic() - started)
        return written

    def stream(self, text: str, *, voice: Optional[str] = None, model: Optional[str] = None,
               format: str = "pcm", language: Optional[str] = None, gone: Optional[threading.Event] = None,
               **extra: Any) -> Iterator[bytes]:
        now, kin = voice_for(text, language or settings()["language"])
        if not now.builtin:
            log.info("murmur-voice: streaming with %s", now.id)
            yield from engines.get(now).stream(text, gone)
            return
        # Its voice is on the way: Piper says this one, as a whole clip.
        with tempfile.TemporaryDirectory() as folder:
            written = _piper(text, os.path.join(folder, "line.wav"), kin)
            log.info("murmur-voice: streaming with piper")
            samples, rate = engines.read_wav(written)
        yield engines.to_stream_rate(samples, rate)

    def warm(self) -> None:
        """A call is starting: have both sides loaded before the first word (Hermes's STT lease only
        warms its own Whisper, and only when that's the provider)."""
        global _calling
        mine = settings()
        _calling = mine
        for kind in ("speak", "listen"):
            if not mine["on"][kind]:
                continue
            # A second voice loads too, if it's here, so the first line in it doesn't wait.
            models = (_mouths(mine["language"]) if kind == "speak" else None) or [running(kind, mine["language"])]
            for model in models:
                try:
                    if model.builtin:
                        store.resume()
                        _warm_stand_in(kind)
                    else:
                        engines.get(model)
                except Exception:
                    log.exception("murmur-voice: couldn't load %s", model.id)

    def release(self) -> None:
        global _calling
        _calling = None
        engines.release()


class Listener(TranscriptionProvider):
    """Hears with X-ASR (or Whisper, until it's downloaded, and in other languages)."""

    @property
    def name(self) -> str:
        return NAME

    @property
    def display_name(self) -> str:
        return "Murmur local voice"

    def is_available(self) -> bool:
        return True  # Whisper ships with the image

    def transcribe(self, file_path: str, *, model: Optional[str] = None, language: Optional[str] = None,
                   **extra: Any) -> dict[str, Any]:
        try:
            now = running("listen", catalog.language_of(language) if language else settings()["language"])
            started = time.monotonic()
            result = None
            if not now.builtin:
                try:
                    text = engines.get(now).transcribe(engines.read_audio(file_path))
                    result = {"success": True, "transcript": text, "provider": NAME}
                except Exception:
                    log.exception("murmur-voice: %s couldn't listen; Whisper stands in", now.id)
                    now = catalog.WHISPER
            if result is None:
                store.resume()
                result = _whisper(file_path)
            log.info("murmur-voice: heard with %s in %.2fs", now.id, time.monotonic() - started)
            return result
        except Exception as e:
            log.exception("murmur-voice: transcription failed")
            return {"success": False, "transcript": "", "provider": NAME, "error": str(e)[:300]}
