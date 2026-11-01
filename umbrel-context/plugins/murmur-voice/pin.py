#!/usr/bin/env python3
"""Writes pins.json: every file a local voice model needs, at an exact revision, with its size and
sha256, and the wheels each engine runs on for x86_64 and aarch64.

Run it on a developer machine (it needs Docker and the network), after changing core/catalog.py
or bumping Hermes, against the image the plugin ships in:

    python3 umbrel-context/plugins/murmur-voice/pin.py --image ghcr.io/getumbrel/hermes-agent-umbrel:v0.22.5-2

Wheels are resolved with Hermes's own uv inside that image, against the image's own environment,
so the packages it already has (numpy, onnxruntime, huggingface_hub, ...) are used as they are and
only the rest is pinned. Hugging Face files are taken at the commits catalog.SOURCES names; files
elsewhere are downloaded once here to hash them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "core"))
import catalog  # noqa: E402

ARCHES = {"x86_64": "x86_64-manylinux_2_40", "aarch64": "aarch64-manylinux_2_40"}  # the image has glibc 2.41
PYTHON = "3.14"
GLIBC = (2, 41)  # the image's (Debian trixie)
TORCH_INDEX = "https://download.pytorch.org/whl/cpu"
UA = {"User-Agent": "murmur-voice-pin/1"}


def get(url: str, *, data: bytes | None = None, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as res:
        return res.read()


def sha_of_url(url: str) -> tuple[int, str]:
    digest, size = hashlib.sha256(), 0
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=600) as res:
        while chunk := res.read(1 << 20):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


# ── Wheels ──────────────────────────────────────────────────────────────────────────────────────

def _uv(image: str, args: list[str], mounts: dict[str, str]) -> str:
    vols = [x for host, inner in mounts.items() for x in ("-v", f"{host}:{inner}")]
    uv = subprocess.run(["docker", "run", "--rm", "--entrypoint", "sh", image, "-c",
                         '/opt/hermes/.venv/bin/python -c "import pm; print(pm.uv_launcher(\'uv\'))"'],
                        check=True, capture_output=True, text=True).stdout.strip()
    out = subprocess.run(["docker", "run", "--rm", *vols, "--entrypoint", uv, image, *args],
                         check=True, capture_output=True, text=True)
    return out.stdout


def resolve(image: str) -> dict[str, dict[str, list[str]]]:
    """{runtime: {arch: ["name==version", ...]}}: what each runtime adds to the image's environment."""
    with tempfile.TemporaryDirectory() as tmp:
        frozen = _uv(image, ["pip", "freeze", "--python", "/opt/hermes/.venv/bin/python"], {})
        have = {}
        for line in frozen.splitlines():
            if "==" in line and not line.startswith("-e"):
                name, version = line.split("==", 1)
                have[norm(name)] = version.strip()
        Path(tmp, "constraints.txt").write_text("".join(f"{n}=={v}\n" for n, v in have.items()))
        out: dict[str, dict[str, list[str]]] = {}
        for runtime, reqs in catalog.RUNTIMES.items():
            Path(tmp, f"{runtime}.in").write_text("\n".join(reqs) + "\n")
            out[runtime] = {}
            for arch, platform in ARCHES.items():
                text = _uv(image, ["pip", "compile", f"/lock/{runtime}.in", "-c", "/lock/constraints.txt",
                                   "--python-version", PYTHON, "--python-platform", platform,
                                   "--index-url", "https://pypi.org/simple", "--extra-index-url", TORCH_INDEX,
                                   "--index-strategy", "unsafe-best-match", "--no-header", "--no-annotate", "-q"],
                           {tmp: "/lock"})
                pins = []
                for line in text.splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "==" not in line:
                        continue
                    name, version = line.split("==", 1)
                    if have.get(norm(name)) == version.strip():
                        continue
                    pins.append(f"{norm(name)}=={version.strip()}")
                out[runtime][arch] = sorted(pins)
        return out


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _tags(filename: str) -> tuple[str, str, str]:
    parts = filename[:-4].split("-")
    return parts[-3], parts[-2], parts[-1]


def _fits(filename: str, arch: str) -> bool:
    py, abi, plat = _tags(filename)
    pythons = py.split(".")
    if not any(p in ("cp314", "py3", "py314") or (p.startswith("cp3") and abi == "abi3" and int(p[3:]) <= 14)
               for p in pythons):
        return False
    if abi not in ("none", "abi3", "cp314"):
        return False
    for p in plat.split("."):
        if p == "any":
            return True
        m = re.match(r"manylinux_(\d+)_(\d+)_(.+)$", p) or re.match(r"manylinux(2014)_(.+)$", p)
        if not m:
            continue
        if m.re.pattern.startswith("manylinux(2014)"):
            glibc, tail = (2, 17), m.group(2)
        else:
            glibc, tail = (int(m.group(1)), int(m.group(2))), m.group(3)
        if tail == arch and glibc <= GLIBC:
            return True
    return False


def wheel(pin: str, arch: str) -> dict:
    name, version = pin.split("==")
    if "+cpu" in version:  # torch's CPU build lives on its own index
        page = get(f"{TORCH_INDEX}/{name}/").decode()
        for href in re.findall(r'href="([^"]+)"', page):
            url = urllib.parse.urljoin(f"{TORCH_INDEX}/{name}/", href)
            filename = urllib.parse.unquote(url.split("/")[-1].split("#")[0])
            if filename.startswith(f"{name}-{version}-") and filename.endswith(".whl") and _fits(filename, arch):
                sha = url.split("#sha256=")[1]
                size = int(urllib.request.urlopen(urllib.request.Request(url.split("#")[0], method="HEAD",
                                                                        headers=UA)).headers["Content-Length"])
                return {"name": filename, "url": url.split("#")[0], "size": size, "sha256": sha}
        raise SystemExit(f"no {pin} wheel for {arch}")
    meta = json.loads(get(f"https://pypi.org/pypi/{name}/{version}/json"))
    files = [u for u in meta["urls"] if u["filename"].endswith(".whl") and _fits(u["filename"], arch)]
    if not files:
        raise SystemExit(f"no {pin} wheel for {arch} on Python {PYTHON}")
    # Prefer a wheel built for this Python over a generic one.
    files.sort(key=lambda u: ("cp314" not in u["filename"], "any" in u["filename"]))
    u = files[0]
    return {"name": u["filename"], "url": u["url"], "size": u["size"], "sha256": u["digests"]["sha256"]}


# ── Model files ─────────────────────────────────────────────────────────────────────────────────

def hf_file(repo: str, path: str, revision: str) -> dict:
    url = f"https://huggingface.co/{repo}/resolve/{revision}/{urllib.parse.quote(path)}"
    info = json.loads(get(f"https://huggingface.co/api/models/{repo}/paths-info/{revision}",
                          data=urllib.parse.urlencode({"paths": path, "expand": "true"}).encode(),
                          headers={"Content-Type": "application/x-www-form-urlencoded"}))
    if not info:
        raise SystemExit(f"{repo}@{revision} has no {path}")
    lfs = info[0].get("lfs") or {}
    if lfs.get("oid"):
        return {"url": url, "size": int(lfs["size"]), "sha256": lfs["oid"]}
    size, sha = sha_of_url(url)
    return {"url": url, "size": size, "sha256": sha}


def github_asset(url: str) -> dict:
    m = re.match(r"https://github.com/([^/]+/[^/]+)/releases/download/([^/]+)/(.+)$", url)
    if not m:
        size, sha = sha_of_url(url)
        return {"url": url, "size": size, "sha256": sha}
    repo, tag, name = m.groups()
    release = json.loads(get(f"https://api.github.com/repos/{repo}/releases/tags/{tag}"))
    for asset in release["assets"]:
        if asset["name"] == name and str(asset.get("digest", "")).startswith("sha256:"):
            return {"url": url, "size": asset["size"], "sha256": asset["digest"].split(":", 1)[1]}
    size, sha = sha_of_url(url)
    return {"url": url, "size": size, "sha256": sha}


def pack(source: catalog.Source) -> dict:
    out: dict = {"files": []}
    if source.archive:
        out["archive"] = github_asset(source.archive)
        out["keep"] = list(source.keep)
    for path, dest in source.files:
        if path.startswith("https://"):
            size, sha = sha_of_url(path)
            out["files"].append({"path": dest, "url": path, "size": size, "sha256": sha})
            continue
        file, _, revision = path.partition("@")
        out["files"].append({"path": dest, **hf_file(source.repo, file, revision or source.revision)})
    if source.extra:
        out["extra"] = source.extra
    out["size"] = sum(f["size"] for f in out["files"]) + (out.get("archive") or {}).get("size", 0)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", required=True, help="the Hermes image the plugin ships in")
    ap.add_argument("--only", choices=("runtimes", "packs"), help="refresh just one half")
    args = ap.parse_args()
    target = HERE / "pins.json"
    pins = json.loads(target.read_text()) if target.exists() else {}
    pins["python"] = PYTHON
    if args.only != "packs":
        runtimes = {}
        for runtime, arches in resolve(args.image).items():
            runtimes[runtime] = {arch: [wheel(p, arch) for p in reqs] for arch, reqs in arches.items()}
            print(runtime, {a: f"{sum(w['size'] for w in ws) / 1e6:.0f} MB" for a, ws in runtimes[runtime].items()})
        pins["runtimes"] = runtimes
    if args.only != "runtimes":
        packs = {}
        for key, source in catalog.SOURCES.items():
            packs[key] = pack(source)
            print(key, f"{packs[key]['size'] / 1e6:.0f} MB")
        pins["packs"] = packs
    target.write_text(json.dumps(pins, indent=1, sort_keys=True) + "\n")
    print("wrote", target)


if __name__ == "__main__":
    main()
