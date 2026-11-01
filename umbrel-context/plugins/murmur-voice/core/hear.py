"""Hearing a call from an iPhone. Any Web Audio there makes speech crackle (encode.py), and the
levels the phone can read without it don't say when someone has finished: echo cancelling moves
them from one moment to the next. So the phone records and sends the mic here as it goes, and this
decides. Silero's voice activity model, which comes with Hermes's Whisper, listens for speech; a
turn ends once it has stopped, and goes back as a WAV for Hermes's own transcription.

The phone still notices being talked over itself, from the mic's level: the model would hear the
agent's own voice in what echo cancelling leaves behind.
"""

from __future__ import annotations

import importlib.util
import logging
import queue
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional, Union

from . import engines

log = logging.getLogger("murmur_voice")

RATE = engines.SAMPLE_RATE_IN
FRAME = 512  # samples at a time, what the model takes
FRAME_MS = FRAME * 1000 // RATE
CONTEXT = 64  # samples of the frame before, which it also wants
SPEECH, STILL_SPEECH = 0.5, 0.35  # how sure it has to be, to start and to carry on
START_MS = 96  # shorter than this is a click
END_SILENCE_MS = 750  # a pause this long ends your turn
MIN_SPEECH_MS = 280  # less than this is a cough, not a turn
MAX_TURN_MS = 15_000  # past this the turn is sent anyway: a call never sits there listening
LEAD_MS = 640  # kept before speech starts, so the first word is whole
RECENT_MS = 8_000  # what a tap sends when nobody seemed to be talking
GIVE_UP_MS = 4_000  # talked over, then nothing: it was the room
STALLED_SECONDS = 2.0  # no audio for this long during a turn: it gets what it has

Send = Callable[[Union[dict, bytes, None]], None]  # None: this ear has stopped
_model: Any = None
_model_lock = threading.Lock()


def _session() -> Any:
    global _model
    with _model_lock:
        if _model is None:
            import onnxruntime

            spec = importlib.util.find_spec("faster_whisper")
            path = Path(spec.origin).parent / "assets" / "silero_vad_v6.onnx"
            options = onnxruntime.SessionOptions()
            options.inter_op_num_threads = options.intra_op_num_threads = 1
            options.log_severity_level = 4
            _model = onnxruntime.InferenceSession(str(path), providers=["CPUExecutionProvider"], sess_options=options)
        return _model


class Vad:
    """How likely each frame is to be speech. It remembers what came before, so one per call."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        import numpy as np

        self.h = np.zeros((1, 1, 128), np.float32)
        self.c = np.zeros((1, 1, 128), np.float32)
        self.context = np.zeros(CONTEXT, np.float32)

    def __call__(self, frame: Any) -> float:
        import numpy as np

        audio = np.concatenate([self.context, frame])[None]
        speech, self.h, self.c = _session().run(None, {"input": audio, "h": self.h, "c": self.c})
        self.context = frame[-CONTEXT:]
        return float(speech.reshape(-1)[0])


class Turns:
    """Whose turn it is, a frame at a time, in the modes Murmur's own mic has: "listen" while it's
    your turn, "barge" while the agent speaks, "off" while it thinks."""

    def __init__(self, send: Send) -> None:
        self.send = send
        self.mode = "off"
        self.recent: deque = deque(maxlen=RECENT_MS // FRAME_MS)
        self._clear()

    def _clear(self) -> None:
        self.frames: Optional[list] = None  # a turn under way
        self.onset = self.spoke = self.quiet = 0

    def set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self._clear()

    def _lead(self) -> list:
        return list(self.recent)[-LEAD_MS // FRAME_MS:]

    def interrupted(self) -> None:
        """You talked over the agent, and the phone noticed before this heard it."""
        self.mode = "listen"
        self._clear()
        self.frames = self._lead()

    def finish(self) -> None:
        """A tap: this is your turn, as it stands. If nobody seemed to be talking, the last few seconds."""
        frames = self.frames if self.frames else list(self.recent)
        self._clear()
        self._hand_over(frames)

    def stalled(self) -> None:
        """The audio has stopped arriving mid-turn. It won't end by itself now."""
        frames, spoke = self.frames, self.spoke
        if frames is None:
            return
        self._clear()
        if spoke >= MIN_SPEECH_MS:
            log.info("murmur-voice: the mic stopped mid-turn")
            self._hand_over(frames)
        else:
            self.send({"type": "nothing"})

    def _hand_over(self, frames: list) -> None:
        import numpy as np

        if not frames:
            return self.send({"type": "nothing"})
        pcm = (np.clip(np.concatenate(frames), -1.0, 1.0) * 32767).astype("<i2").tobytes()
        self.send(engines.to_wav(pcm, RATE))

    def frame(self, samples: Any, speech: float) -> None:
        self.recent.append(samples)
        if self.mode != "listen":
            return
        if self.frames is None:
            self.onset = self.onset + FRAME_MS if speech >= SPEECH else 0
            if self.onset >= START_MS:
                self.frames = self._lead()
                self.spoke = self.onset
                self.send({"type": "speech"})
            return
        self.frames.append(samples)
        if speech >= (STILL_SPEECH if self.spoke else SPEECH):
            self.spoke += FRAME_MS
            self.quiet = 0
        else:
            self.quiet += FRAME_MS
        length = len(self.frames) * FRAME_MS
        said = self.spoke >= MIN_SPEECH_MS
        if not self.spoke and length >= GIVE_UP_MS or self.spoke and not said and self.quiet >= END_SILENCE_MS:
            self._clear()
            self.send({"type": "nothing"})
        elif said and self.quiet >= END_SILENCE_MS or length >= MAX_TURN_MS:
            frames = self.frames
            log.info("murmur-voice: a turn of %.1fs ended here", length / 1000)
            self._clear()
            self._hand_over(frames)


class Ear:
    """One call's mic: what the phone's recorder writes goes in as it's written, in order, along
    with what the call asks for. Decoding and listening happen in a thread of its own."""

    def __init__(self, send: Send, vad: Optional[Callable[[Any], float]] = None) -> None:
        self.turns = Turns(send)
        self.vad = vad or Vad()
        self.inbox: queue.Queue = queue.Queue()
        self.send = send
        self.held = b""
        self.ended = False
        self.fresh = False  # a new recording is coming, from the top
        self.fed = time.monotonic()
        self.pending: Any = None
        self.thread = threading.Thread(target=self._run, name="murmur-voice-ear", daemon=True)
        self.thread.start()

    def feed(self, item: Union[bytes, dict]) -> None:
        self.inbox.put(item)

    def close(self) -> None:
        self.inbox.put(None)

    def _command(self, command: dict) -> None:
        if command.get("mode") in ("listen", "barge", "off"):
            self.turns.set_mode(command["mode"])
            if command["mode"] == "listen" and hasattr(self.vad, "reset"):
                self.vad.reset()
        elif command.get("interrupted"):
            self.turns.interrupted()
            if hasattr(self.vad, "reset"):
                self.vad.reset()
        elif command.get("finish"):
            self.turns.finish()
        elif command.get("restart"):
            self.fresh = True

    def read(self, size: int = -1) -> bytes:
        """What the decoder reads the recording through. Asks are answered on the way past."""
        while not self.held:
            if self.fresh:
                return b""  # the end of this recording: _run opens the next
            try:
                # (It reads again after the end, and mustn't wait for more.)
                item = None if self.ended else self.inbox.get(timeout=0.25)
            except queue.Empty:
                if time.monotonic() - self.fed >= STALLED_SECONDS:
                    self.turns.stalled()
                continue
            if item is None:
                self.ended = True
                return b""
            if isinstance(item, dict):
                self._command(item)
            else:
                self.held, self.fed = item, time.monotonic()
        size = len(self.held) if size < 0 else size
        out, self.held = self.held[:size], self.held[size:]
        return out

    def _hear(self, samples: Any) -> None:
        import numpy as np

        self.pending = samples if self.pending is None else np.concatenate([self.pending, samples])
        while len(self.pending) >= FRAME:
            frame, self.pending = self.pending[:FRAME], self.pending[FRAME:]
            self.turns.frame(frame, self.vad(frame) if self.turns.mode == "listen" else 0.0)

    def _decode(self) -> None:
        import av

        # (Told not to read ahead, or it waits for more of the recording than a turn takes.)
        options = {"fflags": "+nobuffer", "probesize": "4096", "analyzeduration": "0"}
        with av.open(self, mode="r", options=options) as recording:
            resampler = av.AudioResampler(format="flt", layout="mono", rate=RATE)
            for frame in recording.decode(audio=0):
                for out in resampler.resample(frame):
                    self._hear(out.to_ndarray().reshape(-1))

    def _run(self) -> None:
        try:
            while not self.ended:
                self.fresh = False
                try:
                    self._decode()
                except Exception:
                    # A recording cut off by a new one, or by a phone hanging up.
                    if not (self.fresh or self.ended):
                        raise
        except Exception as e:
            log.warning("murmur-voice: stopped hearing the call: %s", str(e)[:200])
            self.send(None)
