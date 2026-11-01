"""Downloads: the model files and Python packages local voices run on, kept under Hermes's data
folder (``<HERMES_HOME>/murmur-voice``) so an app update doesn't fetch them again.

Every file is pinned in pins.json by size and sha256 and checked before it's used. Downloads
resume where they stopped (a ``.part`` file per sha256), land in a staging folder, and only move
into place once everything in them checks out, so a half-finished download is never mistaken for
a model. One download runs at a time, in a thread of its own; asking for more while it runs adds
them to it. What was asked for is remembered (``wanted.json``) and picked up again after a restart.

    murmur-voice/
      models/<model>/             e.g. models/pocket-tts-18m, models/x-asr
      runtimes/<engine>/<arch>-<pin>/   wheels unpacked, added to sys.path after Hermes's own
      partial/<sha256>.part
      wanted.json
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

from . import catalog

log = logging.getLogger("murmur_voice")

PINS: dict[str, Any] = json.loads((Path(__file__).resolve().parent.parent / "pins.json").read_text())
ARCH = {"amd64": "x86_64", "arm64": "aarch64"}.get(platform.machine().lower(), platform.machine().lower())
MARK = ".pinned"
CHUNK = 1 << 20
ATTEMPTS = 4


def root() -> Path:
    """``<HERMES_HOME>/murmur-voice``, shared by every agent (models belong to the machine)."""
    base = ""
    try:
        from hermes_constants import get_default_hermes_root

        base = str(get_default_hermes_root())
    except Exception:
        base = os.environ.get("HERMES_HOME", "") or str(Path.home() / ".hermes")
    return Path(os.environ.get("MURMUR_VOICE_HOME") or Path(base) / "murmur-voice")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


# ── What's there ────────────────────────────────────────────────────────────────────────────────

def pack_dir(key: str) -> Path:
    return root() / "models" / key


def pack_pin(key: str) -> Optional[dict]:
    return (PINS.get("packs") or {}).get(key)


def runtime_pin(name: str) -> Optional[list[dict]]:
    return ((PINS.get("runtimes") or {}).get(name) or {}).get(ARCH)


def runtime_dir(name: str) -> Optional[Path]:
    wheels = runtime_pin(name)
    if wheels is None:
        return None
    tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    return root() / "runtimes" / name / f"{ARCH}-{tag}-{_digest(wheels)}"


def _marked(folder: Optional[Path], pin: Any) -> bool:
    if folder is None or pin is None:
        return False
    try:
        return (folder / MARK).read_text().strip() == _digest(pin)
    except OSError:
        return False


def pack_ready(key: str) -> bool:
    return _marked(pack_dir(key), pack_pin(key))


def runtime_ready(name: str) -> bool:
    return _marked(runtime_dir(name), runtime_pin(name))


def ready(model: catalog.Model) -> bool:
    """Whether ``model`` can run here now: it came with the image, or it's downloaded with its packages."""
    return model.builtin or (pack_ready(model.id) and (model.runtime is None or runtime_ready(model.runtime)))


def supported(model: catalog.Model) -> bool:
    """Pinned for this machine's architecture (a model with no wheels here can't run)."""
    return model.builtin or (pack_pin(model.id) is not None and runtime_pin(model.runtime or "") is not None)


def needs(models: list[catalog.Model]) -> list[tuple[str, str, int]]:
    """What's still to fetch for these models: ("runtime"|"pack", key, bytes), each thing once."""
    out: list[tuple[str, str, int]] = []
    for model in models:
        if model.builtin:
            continue
        if model.runtime and not runtime_ready(model.runtime):
            out.append(("runtime", model.runtime, sum(w["size"] for w in runtime_pin(model.runtime) or [])))
        if not pack_ready(model.id):
            out.append(("pack", model.id, int((pack_pin(model.id) or {}).get("size") or 0)))
    return list(dict.fromkeys(out))


# ── Fetching ────────────────────────────────────────────────────────────────────────────────────

class Failed(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind, self.detail = kind, detail


class Job:
    """The one download running, as Murmur sees it."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None
        self.items: list[tuple[str, str, int]] = []
        self.done_bytes = 0  # finished items
        self.part_bytes = 0  # the item under way
        self.state: Optional[str] = None  # downloading, unpacking, installing, done, failed
        self.current: Optional[tuple[str, str]] = None
        self.error: Optional[dict] = None
        self.samples: list[tuple[float, int]] = []
        self.finished_at = 0.0

    def snapshot(self) -> Optional[dict]:
        with self.lock:
            if self.state is None:
                return None
            total = sum(i[2] for i in self.items)
            received = min(total, self.done_bytes + self.part_bytes)
            speed = 0.0
            if len(self.samples) >= 2:
                (t0, b0), (t1, b1) = self.samples[0], self.samples[-1]
                speed = (b1 - b0) / max(0.001, t1 - t0)
            return {
                "state": self.state,
                "items": [{"kind": k, "id": key, "size": size} for k, key, size in self.items],
                "current": {"kind": self.current[0], "id": self.current[1]} if self.current else None,
                "received": received,
                "total": total,
                "speed": round(speed),
                "eta": round((total - received) / speed) if speed > 0 and self.state == "downloading" else None,
                "error": self.error,
            }

    def progress(self, part: int) -> None:
        with self.lock:
            self.part_bytes = part
            now = time.monotonic()
            self.samples.append((now, self.done_bytes + part))
            self.samples = [s for s in self.samples if now - s[0] <= 6]


job = Job()
_wanted_lock = threading.Lock()
# Called (in the download thread) each time something finishes downloading.
on_installed: list[Callable[[], None]] = []


def _wanted_path() -> Path:
    return root() / "wanted.json"


def wanted() -> list[str]:
    try:
        return [str(m) for m in json.loads(_wanted_path().read_text()).get("models") or []]
    except (OSError, ValueError, AttributeError):
        return []


def _set_wanted(models: list[str]) -> None:
    path = _wanted_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"models": models}))
    os.replace(tmp, path)


def _wanted_models() -> list[catalog.Model]:
    known = {m.id: m for pair in catalog.PAIRS.values() for m in pair.values()}
    return [known[m] for m in wanted() if m in known]


def install(models: list[catalog.Model]) -> Optional[dict]:
    """Fetch what these models need, in the background, alongside anything already wanted."""
    with _wanted_lock:
        try:
            _set_wanted(sorted({*wanted(), *(m.id for m in models if not m.builtin)}))
        except OSError as e:
            with job.lock:
                job.state, job.error = "failed", {"kind": "unknown", "detail": f"can't write to {root()}: {e.strerror}"}
            return job.snapshot()
        _start()
    return job.snapshot()


def resume() -> None:
    """Carry on with what was asked for before a restart, if anything's left. Cheap when nothing is.
    A download that failed stays failed until someone asks again, so Murmur can say why."""
    if job.state == "failed" or (job.thread and job.thread.is_alive()):
        return
    with _wanted_lock:
        if needs(_wanted_models()):
            _start()


def _start() -> None:
    if job.thread and job.thread.is_alive():
        return
    with job.lock:
        job.state, job.error, job.items, job.done_bytes, job.part_bytes, job.samples = "downloading", None, [], 0, 0, []
    job.thread = threading.Thread(target=_run, name="murmur-voice-download", daemon=True)
    job.thread.start()


def _run() -> None:
    finished: list[tuple[str, str, int]] = []
    try:
        while True:
            todo = needs(_wanted_models())
            with job.lock:
                # What this run has finished stays listed, so progress only moves forward.
                job.items = finished + todo
                job.done_bytes = sum(i[2] for i in finished)
                job.part_bytes = 0
            if not todo:
                break
            _check_space(todo)
            item = todo[0]
            with job.lock:
                job.current, job.state = item[:2], "downloading"
            if item[0] == "runtime":
                _install_runtime(item[1])
            else:
                _install_pack(item[1])
            finished.append(item)
            with job.lock:
                job.current = None
            for hook in on_installed:
                try:
                    hook()
                except Exception:
                    log.exception("murmur-voice: after a download")
        with job.lock:
            job.state, job.finished_at, job.part_bytes = "done", time.time(), 0
            job.done_bytes = sum(i[2] for i in job.items)
        _cleanup()
    except Failed as e:
        log.warning("murmur-voice: download stopped (%s): %s", e.kind, e.detail)
        with job.lock:
            job.state, job.error = "failed", {"kind": e.kind, "detail": e.detail}
    except Exception as e:  # anything else still ends the job with a reason
        log.exception("murmur-voice: download failed")
        with job.lock:
            job.state, job.error = "failed", {"kind": "unknown", "detail": str(e)[:300]}


def _check_space(todo: list[tuple[str, str, int]]) -> None:
    folder = root()
    folder.mkdir(parents=True, exist_ok=True)
    # Wheels unpack to about three times their size; models stay about the same.
    need = sum(size * (3 if kind == "runtime" else 1.2) for kind, _, size in todo) + 256 * 1024 * 1024
    free = shutil.disk_usage(folder).free
    if free < need:
        raise Failed("space", f"needs {need / 1e9:.1f} GB free, has {free / 1e9:.1f} GB")


def _fetch(url: str, size: int, sha256: str, on_bytes: Callable[[int], None]) -> Path:
    """One pinned file, resumed if part of it is here already. Returns the verified .part file."""
    partial = root() / "partial"
    partial.mkdir(parents=True, exist_ok=True)
    part = partial / f"{sha256}.part"
    for attempt in range(ATTEMPTS):
        have = part.stat().st_size if part.exists() else 0
        if have > size:
            part.unlink()
            have = 0
        if have < size:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "murmur-voice/1", **({"Range": f"bytes={have}-"} if have else {})})
                with urllib.request.urlopen(req, timeout=60) as res:
                    if have and res.status != 206:  # the server ignored the range: start over
                        have = 0
                    with open(part, "ab" if have else "wb") as f:
                        while chunk := res.read(CHUNK):
                            f.write(chunk)
                            have += len(chunk)
                            on_bytes(have)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
                    raise Failed("space", "the disk filled up")
                if attempt == ATTEMPTS - 1:
                    raise Failed("network", f"{url.split('/')[2]}: {e}")
                time.sleep(2 * (attempt + 1) ** 2)
                continue
        on_bytes(have)
        if have != size:
            continue
        digest = hashlib.sha256()
        with open(part, "rb") as f:
            while chunk := f.read(CHUNK):
                digest.update(chunk)
        if digest.hexdigest() == sha256:
            return part
        part.unlink()  # a corrupt or changed file: fetch it whole again
        if attempt == ATTEMPTS - 1:
            raise Failed("checksum", f"{url.rsplit('/', 1)[-1]} isn't the pinned file")
    raise Failed("network", f"{url.rsplit('/', 1)[-1]} didn't finish downloading")


def _staging(final: Path) -> Path:
    final.parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f".{final.name}-", dir=final.parent))


def _commit(stage: Path, final: Path, pin: Any) -> None:
    (stage / MARK).write_text(_digest(pin))
    old = final.with_name(f".{final.name}-old-{os.getpid()}")
    if final.exists():
        os.replace(final, old)
    os.replace(stage, final)
    shutil.rmtree(old, ignore_errors=True)


def _install_pack(key: str) -> None:
    pin = pack_pin(key)
    if pin is None:
        raise Failed("unknown", f"nothing pinned for {key}")
    final = pack_dir(key)
    stage = _staging(final)
    try:
        offset = 0

        def counter(base: int) -> Callable[[int], None]:
            return lambda n: job.progress(base + n)

        for f in pin.get("files") or []:
            got = _fetch(f["url"], f["size"], f["sha256"], counter(offset))
            dest = stage / f["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(got, dest)
            offset += f["size"]
        if pin.get("archive"):
            a = pin["archive"]
            got = _fetch(a["url"], a["size"], a["sha256"], counter(offset))
            with job.lock:
                job.state = "unpacking"
            _unpack(got, stage, pin.get("keep") or [])
            got.unlink()
        _commit(stage, final, pin)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    with job.lock:
        job.state = "downloading"


def _unpack(archive: Path, dest: Path, keep: list[str]) -> None:
    """A .tar.bz2's members, without its top folder, and only those ``keep`` names (a name, or a
    folder ending in "/"). Anything that would land outside ``dest`` is refused."""
    with tarfile.open(archive, "r:*") as tar:
        for member in tar:
            parts = Path(member.name).parts
            inner = Path(*parts[1:]) if len(parts) > 1 else None
            if inner is None or not (member.isfile() or member.isdir()):
                continue
            rel = inner.as_posix()
            if keep and not any(rel == k or (k.endswith("/") and rel.startswith(k)) for k in keep):
                continue
            target = (dest / inner).resolve()
            if not str(target).startswith(str(dest.resolve()) + os.sep):
                continue
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            with src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, CHUNK)


def _install_runtime(name: str) -> None:
    wheels = runtime_pin(name)
    final = runtime_dir(name)
    if wheels is None or final is None:
        raise Failed("unsupported", f"no {name} packages for {ARCH}")
    stage = _staging(final)
    try:
        offset = 0
        fetched = []
        for w in wheels:
            fetched.append(_fetch(w["url"], w["size"], w["sha256"], lambda n, base=offset: job.progress(base + n)))
            offset += w["size"]
        with job.lock:
            job.state = "installing"
        for path in fetched:
            _unpack_wheel(path, stage)
        _commit(stage, final, wheels)
        for path in fetched:
            path.unlink(missing_ok=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    with job.lock:
        job.state = "downloading"


def _unpack_wheel(path: Path, dest: Path) -> None:
    """A wheel into a flat site folder, the way pip lays out purelib and platlib (scripts and
    headers aren't needed)."""
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            name = info.filename
            parts = name.split("/")
            if parts[0].endswith(".data"):
                if len(parts) < 3 or parts[1] not in ("purelib", "platlib"):
                    continue
                name = "/".join(parts[2:])
            target = (dest / name).resolve()
            if not str(target).startswith(str(dest.resolve()) + os.sep):
                continue
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                os.chmod(target, mode | 0o600)


def _cleanup() -> None:
    """Whatever the pins no longer name (a newer image), and staging left by an interrupted run."""
    folder = root() / "runtimes"
    for name in os.listdir(folder) if folder.exists() else []:
        keep = runtime_dir(name)
        for version in (folder / name).iterdir():
            if version != keep:
                shutil.rmtree(version, ignore_errors=True)
    folder = root() / "models"
    for name in os.listdir(folder) if folder.exists() else []:
        if pack_pin(name) is None:
            shutil.rmtree(folder / name, ignore_errors=True)
