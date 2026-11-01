"""The voices an agent can have on its own machine: one pair per language, the same on every box.

English speaks with Pocket TTS 18M and Chinese with Matcha zh-en, and both listen with X-ASR. All
three run well on anything from a Raspberry Pi up, so there's nothing to choose between. Every
other language stays on Piper and Whisper, which come with the image, as does any language
while its pair downloads.

Every file a pair needs is pinned in pins.json (an exact revision and sha256 for each), written
by pin.py from SOURCES below.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Model:
    id: str
    kind: str  # "speak" or "listen"
    name: str
    # The Python packages it runs on (pins.json "runtimes"); None for one the image ships.
    runtime: str | None = None
    by: str = ""
    license: str = ""
    link: str = ""
    builtin: bool = False


POCKET = Model("pocket-tts-18m", "speak", "Pocket TTS", "pocket", by="Kyutai", license="CC-BY-4.0",
               link="https://huggingface.co/kyutai/pocket-tts")
MATCHA = Model("matcha-zh-en", "speak", "Matcha", "sherpa", by="dengcunqin", license="Apache-2.0",
               link="https://modelscope.cn/models/dengcunqin/matcha_tts_zh_en_20251010")
XASR = Model("x-asr", "listen", "X-ASR", "sherpa", by="Gilgamesh-J", license="Apache-2.0",
             link="https://github.com/Gilgamesh-J/X-ASR")
PIPER = Model("piper", "speak", "Piper", by="Open Home Foundation", license="MIT",
              link="https://github.com/OHF-Voice/piper1-gpl", builtin=True)
WHISPER = Model("whisper", "listen", "Whisper", by="OpenAI", license="MIT",
                link="https://github.com/SYSTRAN/faster-whisper", builtin=True)

BUILT_IN = {"speak": PIPER, "listen": WHISPER}
PAIRS = {"en": {"speak": POCKET, "listen": XASR}, "zh": {"speak": MATCHA, "listen": XASR}}


def language_of(value: object) -> str:
    """A configured language ("en", "en-US", "zh_CN") as a two-letter code, English when there's none."""
    code = str(value or "").strip().lower().replace("_", "-").split("-")[0]
    return code if len(code) == 2 else "en"


# What Piper speaks a language with while its own voice is on the way.
PIPER_VOICES = {"en": "en_US-lessac-medium", "zh": "zh_CN-huayan-medium"}


def sentence_language(text: str, language: str) -> str:
    """Which voice a sentence needs. English and Chinese go by what's written, so an agent set to
    either can say a line in the other (Chinese with English words in it stays Chinese). Every other
    language stays with its own voice."""
    if language not in PAIRS:
        return language
    return "zh" if any("\u3400" <= c <= "\u9fff" or "\uf900" <= c <= "\ufaff" for c in text) else "en"


def pair(language: str) -> dict[str, Model]:
    """What an agent speaks and listens with in this language, once it's downloaded."""
    return PAIRS.get(language, BUILT_IN)


# ── What pin.py pins ────────────────────────────────────────────────────────────────────────────
#
# Hugging Face files are taken at an exact commit; pin.py records each one's size and sha256. Pocket
# TTS's revisions are the ones pocket-tts 4.0.1 itself names in its config, so its weights and voice
# match the package that runs them. Matcha comes from sherpa-onnx's release page, as an archive
# and the vocoder it's made for.

_GITHUB = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
_POCKET = "languages/english_18m_2026-10"
_POCKET_REV = "a7056f505a4a8ca72d8b5fb1440071d6f1a35739"


@dataclass(frozen=True)
class Source:
    """One pack: files from a Hugging Face repo at a commit, files at URLs, and/or one archive."""
    repo: str = ""
    revision: str = ""
    files: tuple[tuple[str, str], ...] = ()  # (path in the repo or URL, path in the pack)
    archive: str = ""  # URL of a .tar.bz2, unpacked into the pack (its top folder dropped)
    keep: tuple[str, ...] = ()  # an archive's files to keep, by name (all when empty)
    extra: dict = field(default_factory=dict)  # what the engine needs to know, kept in pins.json


SOURCES: dict[str, Source] = {
    POCKET.id: Source(
        repo="kyutai/pocket-tts-without-voice-cloning",
        files=(
            (f"{_POCKET}/model.safetensors@{_POCKET_REV}", "model.safetensors"),
            (f"{_POCKET}/tokenizer.model@08ffc58775e62aad9409511b9591c9af626270f3", "tokenizer.model"),
            (f"{_POCKET}/embeddings/alba.safetensors@{_POCKET_REV}", "alba.safetensors"),
        ),
        extra={"config": "english_18m_2026-10", "voice": "alba", "tokenizer": "tokenizer.model"},
    ),
    MATCHA.id: Source(
        archive=f"{_GITHUB}/tts-models/matcha-icefall-zh-en.tar.bz2",
        keep=("model-steps-3.onnx", "lexicon.txt", "tokens.txt", "date-zh.fst", "number-zh.fst", "phone-zh.fst", "espeak-ng-data/"),
        files=((f"{_GITHUB}/vocoder-models/vocos-16khz-univ.onnx", "vocos-16khz-univ.onnx"),),
    ),
    XASR.id: Source(
        repo="csukuangfj2/sherpa-onnx-x-asr-zipformer-transducer-zh-en-punct-int8-2026-06-03",
        revision="ce812cc05d858539a68dd42e86f4fd7a9ce1acf0",
        files=tuple((n, n) for n in ("encoder-epoch-99-avg-1.int8.onnx", "decoder-epoch-99-avg-1.onnx",
                                     "joiner-epoch-99-avg-1.int8.onnx", "tokens.txt")),
    ),
}

# The Python packages each engine runs on, beyond what the image's own environment has. pin.py
# resolves these against that environment with Hermes's uv and pins a wheel per architecture.
# sherpa-onnx stays on 1.13: from 2.0 it no longer reads English words its lexicons don't have,
# and silently drops them from Chinese sentences.
RUNTIMES: dict[str, tuple[str, ...]] = {
    "pocket": ("pocket-tts==4.0.1", "torch==2.14.1+cpu"),
    "sherpa": ("sherpa-onnx==1.13.8",),
}
