#!/usr/bin/env python3
"""Let memory-provider pip deps install into /opt/data instead of /opt/hermes.

Published Hermes images keep /opt/hermes/.venv root-owned and read-only by
design (v0.17+ Docker hardening), so `hermes memory setup`'s runtime
`uv pip install` into that venv's site-packages always fails for the
unprivileged hermes user. Point installs at a persistent, user-writable
directory under HERMES_HOME instead and let PYTHONPATH (set in
docker-compose.yml) make it importable.
"""

from pathlib import Path
import os


MEMORY_SETUP_PATH = Path(
    os.environ.get("HERMES_MEMORY_SETUP_PATH", "/opt/hermes/hermes_cli/memory_setup.py")
)

OLD = '''    uv_path = shutil.which("uv")
    if uv_path:
        install_cmd = [uv_path, "pip", "install", "--python", sys.executable, "--quiet"] + missing
        manual_cmd = f"uv pip install --python {sys.executable} {' '.join(missing)}"'''

NEW = '''    uv_path = shutil.which("uv")
    # Umbrel images keep /opt/hermes/.venv root-owned and read-only; install
    # into a persistent, user-writable directory under HERMES_HOME instead.
    # PYTHONPATH (set in docker-compose.yml) makes it importable at runtime.
    umbrel_target = str(get_hermes_home() / "py-packages")
    if uv_path:
        install_cmd = [uv_path, "pip", "install", "--python", sys.executable, "--quiet", "--target", umbrel_target] + missing
        manual_cmd = f"uv pip install --python {sys.executable} --target {umbrel_target} {' '.join(missing)}"'''

# A caller in the same process (e.g. a provider's own _ensure_sdk_installed())
# may re-import the just-installed package moments later. CPython's FileFinder
# caches a directory's listing and only rechecks it once the directory's mtime
# changes, which can lag the install by up to a second — so invalidate the
# import cache right after a successful install instead of leaving that to
# chance.
INVALIDATE_OLD = '''        print(f"  ✓ Installed {', '.join(missing)}")
    except subprocess.CalledProcessError as e:'''

INVALIDATE_NEW = '''        print(f"  ✓ Installed {', '.join(missing)}")
        import importlib
        importlib.invalidate_caches()
    except subprocess.CalledProcessError as e:'''


def main() -> None:
    text = MEMORY_SETUP_PATH.read_text(encoding="utf-8")
    if NEW in text:
        raise RuntimeError("Umbrel memory-deps patch already applied")
    if OLD not in text:
        raise RuntimeError("Expected upstream _install_dependencies() uv branch not found")
    if INVALIDATE_OLD not in text:
        raise RuntimeError("Expected upstream post-install success block not found")
    text = text.replace(OLD, NEW, 1)
    text = text.replace(INVALIDATE_OLD, INVALIDATE_NEW, 1)
    MEMORY_SETUP_PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
