#!/usr/bin/env python3
"""Size local model windows to the memory that's free, not the memory installed.

On CPU and integrated graphics (Hermes's "unified memory" budget) the managed
llama.cpp runtime picks each model's context window against 80% of the RAM the
machine has (MemTotal), and never narrows it to what other programs leave free:
hardware.launch_budget() returns None for unified memory. On an Umbrel that's
16 GB with ~3 GB free, Qwen3.5 2B launches at its native 256K window; llama.cpp
zeroes the whole q8 KV cache up front, so the server holds ~3.6 GB before the
first token and the kernel kills it (and Hermes) mid-turn.

Two changes, both in hermes_cli/local_runtime/hardware.py:

1. Free RAM is read through the container's cgroup limit as well as
   /proc/meminfo, which inside Docker reports the host's memory, not the
   container's.
2. launch_budget() answers for unified memory too: what's free now plus what
   the outgoing server frees, less the same 20% headroom probe_budget() keeps.
   context_policy.fit_to_free_memory() then narrows the window, never below
   Hermes's 64K floor, exactly as it already does for graphics cards. The idle
   re-plan (bootstrap.refit_idle_presets) and in-session growth pick it up too,
   so a window grows back once memory frees up.
"""

from pathlib import Path
import os


HARDWARE_PATH = Path(
    os.environ.get(
        "HERMES_HARDWARE_PATH", "/opt/hermes/hermes_cli/local_runtime/hardware.py"
    )
)

# 1. cgroup-aware RAM.
RAM_ANCHOR = "def _ram_bytes() -> tuple[int, int]:\n"
CGROUP_HELPER = '''def _cgroup_ram_limit() -> tuple[int, int] | None:
    """(limit, available) under this process's cgroup memory limit, or None when there's none.

    Inside a container /proc/meminfo is the host's, so a limited container would otherwise
    budget memory it can never have. Available counts the cgroup's reclaimable page cache as
    free, like MemAvailable does.
    """
    def read(path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            return None

    for limit_path, usage_path, stat_path, cache_key in (
            ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current",
             "/sys/fs/cgroup/memory.stat", "inactive_file"),
            ("/sys/fs/cgroup/memory/memory.limit_in_bytes",
             "/sys/fs/cgroup/memory/memory.usage_in_bytes",
             "/sys/fs/cgroup/memory/memory.stat", "total_inactive_file")):
        limit, usage = read(limit_path), read(usage_path)
        if not limit or not usage or limit == "max":
            continue
        try:
            limit_b, usage_b = int(limit), int(usage)
        except ValueError:
            continue
        # cgroup v1 reports "no limit" as a page-rounded LONG_MAX.
        if limit_b <= 0 or limit_b >= 1 << 60:
            continue
        cache = 0
        for line in (read(stat_path) or "").splitlines():
            key, _, value = line.partition(" ")
            if key == cache_key:
                try:
                    cache = int(value)
                except ValueError:
                    pass
                break
        return limit_b, max(0, min(limit_b, limit_b - usage_b + cache))
    return None


'''
LINUX_RETURN = """                linux_ram = _linux_ram_from_meminfo(meminfo)
                if linux_ram is not None:
                    return linux_ram
"""
LINUX_RETURN_CGROUP = """                linux_ram = _linux_ram_from_meminfo(meminfo)
                if linux_ram is not None:
                    cgroup = _cgroup_ram_limit()
                    if cgroup is not None:
                        # Free memory only: the total stays the machine's, because Hermes refuses
                        # a model outright when its (GPU-sized) estimate tops the total, and on a
                        # CPU that estimate runs gigabytes over what llama.cpp really holds.
                        return linux_ram[0], min(linux_ram[1], cgroup[1])
                    return linux_ram
"""

# 2. A live budget for unified memory.
UMA_NONE = """    if capacity.uma:
        return None
    vram = _nvidia_vram()
"""
UMA_LIVE = """    if capacity.uma:
        # Unified memory (CPU, integrated graphics): the window comes out of system RAM, so it is
        # sized to what other programs leave free now, with probe_budget's headroom.
        _total, available = _ram_bytes()
        usable = int((available + max(0, own_bytes)) * (1 - _UMA_HEADROOM_FRACTION))
        return replace(capacity, usable_vram_bytes=min(capacity.usable_vram_bytes, usable))
    vram = _nvidia_vram()
"""


def replace_once(text: str, old: str, new: str, what: str) -> str:
    if new in text:
        raise RuntimeError(f"{what} already applied")
    if text.count(old) != 1:
        raise RuntimeError(f"Expected upstream {what} anchor not found exactly once")
    return text.replace(old, new)


def main() -> None:
    text = HARDWARE_PATH.read_text(encoding="utf-8")
    text = replace_once(text, RAM_ANCHOR, CGROUP_HELPER + RAM_ANCHOR, "cgroup RAM helper")
    text = replace_once(text, LINUX_RETURN, LINUX_RETURN_CGROUP, "cgroup RAM clamp")
    text = replace_once(text, UMA_NONE, UMA_LIVE, "unified-memory launch budget")
    HARDWARE_PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
