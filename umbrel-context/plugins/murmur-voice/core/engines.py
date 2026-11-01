"""The models themselves, loaded from what store.py downloaded and kept warm while they're used.

Each engine's packages live in their own folder (store.runtime_dir), added to the end of sys.path
so Hermes's own copies of anything shared (numpy, onnxruntime, tokenizers) still win. Nothing here
reaches the network: Pocket TTS gets a config pointing at the downloaded files, and sherpa-onnx is
handed its files' paths.

Each engine stays loaded while it's in use, and is let go after IDLE seconds unused or when a call
ends (the TTS lease's release). A call can use two voices, a sentence at a time.
"""

from __future__ import annotations

import io
import logging
import os
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any, Iterator, Optional

from . import catalog, store

log = logging.getLogger("murmur_voice")

IDLE = int(os.environ.get("MURMUR_VOICE_IDLE_SECONDS", "900"))
SAMPLE_RATE_IN = 16000
# Everything leaves at one rate: Hermes asks for the rate before it knows the sentence.
STREAM_RATE = 24000


def threads() -> int:
    """Cores for one model: all of a small box, never more than 4 (more doesn't help these)."""
    try:
        cores = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cores = os.cpu_count() or 1
    return max(1, min(4, cores))


def _use_runtime(name: Optional[str]) -> None:
    if not name:
        return
    folder = store.runtime_dir(name)
    if folder is None or not store.runtime_ready(name):
        raise RuntimeError(f"{name} isn't installed")
    path = str(folder)
    if path not in sys.path:
        sys.path.append(path)


# ── Speaking ────────────────────────────────────────────────────────────────────────────────────

class Mouth:
    """Speaks raw 16-bit mono PCM at STREAM_RATE, whatever the model makes."""

    def stream(self, text: str, gone: Optional[threading.Event] = None) -> Iterator[bytes]:
        """As it's made, until ``gone`` is set: the listener has left, and the next sentence is waiting."""
        raise NotImplementedError

    def wav(self, text: str) -> bytes:
        return to_wav(b"".join(self.stream(text)), STREAM_RATE)


def to_stream_rate(samples: Any, rate: int) -> bytes:
    """A finished clip as PCM at STREAM_RATE, resampled through the frequency domain."""
    import numpy as np

    samples = np.asarray(samples, dtype=np.float64)
    if rate != STREAM_RATE and len(samples):
        length = round(len(samples) * STREAM_RATE / rate)
        samples = np.fft.irfft(np.fft.rfft(samples), length) * length / len(samples)
    return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()


class Pocket(Mouth):
    """Kyutai's Pocket TTS 18M (English), which starts a sentence in a fraction of a second and streams."""

    def __init__(self, folder: Path, extra: dict) -> None:
        _use_runtime("pocket")
        # (pocket-tts keeps torch to one thread and decodes on a second: more threads only slow it.)
        from pocket_tts import TTSModel
        from pocket_tts.models import tts_model

        config = folder / "config.yaml"
        if not config.exists():
            import yaml

            source = Path(tts_model.CONFIGS_DIR) / f"{extra['config']}.yaml"
            cfg = yaml.safe_load(source.read_text())
            cfg["weights_path"] = str(folder / "model.safetensors")
            cfg["weights_path_without_voice_cloning"] = None
            cfg["flow_lm"]["lookup_table"]["tokenizer_path"] = str(folder / extra["tokenizer"])
            config.write_text(yaml.safe_dump(cfg))
        self.model = TTSModel.load_model(config=str(config))
        self.voice = self.model.get_state_for_audio_prompt(folder / f"{extra['voice']}.safetensors")
        self.lock = threading.Lock()

    def stream(self, text: str, gone: Optional[threading.Event] = None) -> Iterator[bytes]:
        """Raw 16-bit mono PCM, a piece at a time as it's made."""
        import numpy as np

        stop = gone or threading.Event()
        while not self.lock.acquire(timeout=0.05):
            if stop.is_set():  # talked over while waiting its turn
                return
        try:
            for chunk in self.model.generate_audio_stream(self.voice, text, stop=stop):
                audio = np.clip(chunk.detach().cpu().numpy().reshape(-1), -1.0, 1.0)
                yield (audio * 32767).astype("<i2").tobytes()
        finally:
            stop.set()  # the listener went away (talked over it): stop making more
            self.lock.release()


class Matcha(Mouth):
    """Matcha zh-en through sherpa-onnx: Mandarin, with English words inside it, about 8× faster than
    it speaks. Too quick to need streaming, so a sentence comes whole."""

    def __init__(self, folder: Path, extra: dict) -> None:
        _use_runtime("sherpa")
        import sherpa_onnx

        model = sherpa_onnx.OfflineTtsMatchaModelConfig(
            acoustic_model=str(folder / "model-steps-3.onnx"), vocoder=str(folder / "vocos-16khz-univ.onnx"),
            lexicon=str(folder / "lexicon.txt"), tokens=str(folder / "tokens.txt"), data_dir=str(folder / "espeak-ng-data"))
        self.tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(matcha=model, num_threads=threads(), provider="cpu"),
            rule_fsts=",".join(str(folder / f) for f in ("phone-zh.fst", "date-zh.fst", "number-zh.fst"))))
        self.lock = threading.Lock()

    def stream(self, text: str, gone: Optional[threading.Event] = None) -> Iterator[bytes]:
        with self.lock:
            if gone and gone.is_set():
                return
            audio = self.tts.generate(text, sid=0, speed=1.0)
        yield to_stream_rate(audio.samples, audio.sample_rate)


# ── Listening ───────────────────────────────────────────────────────────────────────────────────

class XAsr:
    """X-ASR zh-en through sherpa-onnx: the finished turn, in English, Chinese or a mix, punctuated."""

    def __init__(self, folder: Path, extra: dict) -> None:
        _use_runtime("sherpa")
        import sherpa_onnx

        self.recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(folder / "encoder-epoch-99-avg-1.int8.onnx"), decoder=str(folder / "decoder-epoch-99-avg-1.onnx"),
            joiner=str(folder / "joiner-epoch-99-avg-1.int8.onnx"), tokens=str(folder / "tokens.txt"),
            num_threads=threads(), decoding_method="greedy_search")
        self.lock = threading.Lock()

    def transcribe(self, samples: Any) -> str:
        with self.lock:
            s = self.recognizer.create_stream()
            s.accept_waveform(SAMPLE_RATE_IN, samples)
            self.recognizer.decode_stream(s)
            return s.result.text.strip()


ENGINES = {catalog.POCKET.id: Pocket, catalog.MATCHA.id: Matcha, catalog.XASR.id: XAsr}


# ── Keeping them loaded ─────────────────────────────────────────────────────────────────────────

class _Slot:
    def __init__(self) -> None:
        self.key: Optional[str] = None
        self.engine: Any = None
        self.used = 0.0
        self.lock = threading.Lock()


_slots = {model_id: _Slot() for model_id in ENGINES}
_watcher: Optional[threading.Thread] = None


def get(model: catalog.Model) -> Any:
    """The loaded engine for this model, loading it if needed."""
    slot = _slots[model.id]
    with slot.lock:
        if slot.key != model.id:
            started = time.monotonic()
            extra = (store.pack_pin(model.id) or {}).get("extra") or {}
            slot.engine = ENGINES[model.id](store.pack_dir(model.id), extra)
            slot.key = model.id
            log.info("murmur-voice: loaded %s in %.1fs", model.id, time.monotonic() - started)
        slot.used = time.monotonic()
        _watch()
        return slot.engine


def loaded() -> list[str]:
    return [slot.key for slot in _slots.values() if slot.key]


def release() -> None:
    for slot in _slots.values():
        with slot.lock:
            slot.engine, slot.key = None, None


def _watch() -> None:
    global _watcher
    if _watcher and _watcher.is_alive():
        return

    def run() -> None:
        while any(s.engine is not None for s in _slots.values()):
            time.sleep(30)
            for slot in _slots.values():
                with slot.lock:
                    if slot.engine is not None and time.monotonic() - slot.used > IDLE:
                        log.info("murmur-voice: letting %s go after %ds unused", slot.key, IDLE)
                        slot.engine, slot.key = None, None

    _watcher = threading.Thread(target=run, name="murmur-voice-idle", daemon=True)
    _watcher.start()


# ── Audio ───────────────────────────────────────────────────────────────────────────────────────

def read_audio(path: str) -> Any:
    """Any recording as 16 kHz mono float32 samples (PyAV, which Hermes's Whisper uses too)."""
    import numpy as np

    try:
        with wave.open(path, "rb") as w:
            if w.getsampwidth() == 2 and w.getnchannels() == 1 and w.getframerate() == SAMPLE_RATE_IN:
                return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768.0
    except (wave.Error, EOFError, OSError):
        pass
    import av

    chunks = []
    with av.open(path) as f:
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE_IN)
        for frame in f.decode(audio=0):
            chunks.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(frame))
        chunks.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(None))
    return np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, np.float32)


def read_wav(path: str) -> tuple[Any, int]:
    """A WAV file's samples, as floats, and its rate."""
    import numpy as np

    with wave.open(path, "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float64) / 32768.0, w.getframerate()


def to_wav(pcm: bytes, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()
