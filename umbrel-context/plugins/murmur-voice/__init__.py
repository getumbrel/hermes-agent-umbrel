"""murmur-voice: Hermes's ``murmur`` text-to-speech and speech-to-text providers (core/speech.py).

The rest of the plugin is its dashboard API (dashboard/plugin_api.py): the local voices' status,
recommendation and downloads for Murmur, and Murmur's voice calls on a ChatGPT plan.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

CORE = "murmur_voice_core"


def load():
    """core/, once per process, under one name shared with the dashboard API."""
    if CORE in sys.modules:
        return sys.modules[CORE]
    folder = Path(__file__).resolve().parent / "core"
    spec = importlib.util.spec_from_file_location(CORE, folder / "__init__.py", submodule_search_locations=[str(folder)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[CORE] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(CORE, None)
        raise
    return module


def register(ctx) -> None:
    load()
    from murmur_voice_core import speech

    ctx.register_tts_provider(speech.Speaker())
    ctx.register_transcription_provider(speech.Listener())
