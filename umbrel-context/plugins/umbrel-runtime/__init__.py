from pathlib import Path
import os
import re

CONTEXT_FILE = Path(__file__).resolve().with_name("RUNTIME_CONTEXT.md")


def _load_runtime_context():
    text = CONTEXT_FILE.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"{CONTEXT_FILE} is empty")
    return _format_context(text)


def _format_context(text):
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r"^#\s+", lines[0]):
        lines[0] = re.sub(r"^#\s+", "", lines[0]).rstrip(":")
        while len(lines) > 1 and not lines[1].strip():
            lines.pop(1)
    if lines:
        lines[0] = lines[0].rstrip(":") + ":"
    return "\n".join(lines).strip()


def _inject_umbrel_runtime_context(**kwargs):
    context = _load_runtime_context()
    platform = str(kwargs.get("platform") or "").strip().lower()
    source = os.environ.get("HERMES_SESSION_SOURCE", "").strip().lower()

    if source == "umbrel-web":
        context += (
            "\n- Current chat surface: Umbrel web terminal in the `web` container."
        )
    elif platform and platform != "cli":
        context += (
            f"\n- Current chat surface: Hermes messaging gateway platform `{platform}` "
            "in the `gateway` container."
        )

    return {"context": context}


def register(ctx):
    ctx.register_hook("pre_llm_call", _inject_umbrel_runtime_context)
