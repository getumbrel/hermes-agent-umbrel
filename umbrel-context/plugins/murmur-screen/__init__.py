"""murmur-screen: lets an agent hand its browser to the person, through Hermes's supported plugin surfaces.

When the agent's browser runs on its Bot Screen, the person can watch it live and take it over
(Hermes's display lease). One tool makes that a conversation instead of a dead end:

  ask_for_a_hand   as a last resort, the agent asks the person to do a step only they can (like
                   proving they're human), and waits, keeping the page open, until they take over
                   and hand the browser back. Then it finishes the task by itself.

Murmur draws the ask as a card with the live browser and a Take over button, from the tool call
itself (its `reason`). The tool is offered to an agent whose browser runs on its screen, and only
waits in a chat from an app that shows the screen (Murmur, Hermes Desktop): from Telegram or a
routine nobody could take over, so it says so at once.

Hermes lists plugin tools behind its tool search, and an agent seldom goes looking for a way to ask
(Hermes keeps its own clarify tool out of search for that reason). So a chat that can show the
screen also starts with a few lines, Hermes's supported prompt section: the agent works for the
person, so it finds another way first, with Hermes's browser_vault for passwords and codes, and asks
only when the task truly needs them.

It also opens the agent's browser the way the person watches it, maximized on the screen without
Chrome for Testing's bar (its launcher, `chromium`), so that's the same on any Hermes, and keeps the
agent's own tab in front there.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, MutableMapping, Optional

log = logging.getLogger(__name__)

TOOL = "ask_for_a_hand"
TOOLSET = "screen"
# The session source of a chat from an app that shows the screen (Murmur, Hermes Desktop). Nobody can
# take over from Telegram or a routine.
SHOWN_IN = frozenset({"desktop"})
# How long to wait for the person to arrive. Once they're in control there's no limit: they may be
# halfway through signing in. It matches the screen's own idle stop (bot_desktop.idle_stop_minutes).
WAIT_S = 30 * 60
TICK_S = 1.0

SCHEMA = {
    "name": TOOL,
    # The first sentence is what Hermes's tool search lists, so it says it all.
    "description": (
        "Last resort: let the person take over your browser. For one step only they can do, they watch "
        "it live and hand it back. This waits for them, so keep the page open and don't end your reply. "
        "Then check the page and finish the task yourself."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": 'What they need to do, in a short sentence, e.g. "Solve the check on shop.example.com".',
            },
        },
        "required": ["reason"],
    },
}

TOOK_OVER = "They took over and handed your browser back. Look at the page again before carrying on: they may have changed it."
NOBODY = "Nobody took over in 30 minutes. Tell them in your reply what you need; your browser stays open for a little while."
STOPPED = "The person stopped you while you waited."
NO_SCREEN = "Your browser isn't on a screen the person can see right now. Tell them in your reply what you need instead."
NOT_HERE = "The person can't take over your browser from this chat. Tell them in your reply what you need instead."


# The few lines a chat that can show the screen starts with (Hermes adds its own heading). Asking is
# the last resort: the agent works for the person. Passwords and codes go through Hermes's vault, which
# rides with the browser.
WHEN_TO_ASK = (
    "You work for the person: a blocked page isn't a blocked task. Find another way, and use "
    "`browser_vault` for passwords and codes. Only if the task truly depends on this page and needs "
    f"them personally, like proving they're human, call `{TOOL}` (through `tool_call` if needed)."
)


def _result(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


# ── What the tool needs from Hermes ─────────────────────────────────────────────────────────────
# Each piece is looked up when it's used and guarded, so a Hermes that moved one fails soft (the
# tool still waits for the hand back, it just can't keep a quiet page open). Murmur's contract
# suite covers every one of them against a real Hermes.


def _browser_on_screen() -> bool:
    """The agent's browser opens on its screen: headed (browser.headed, or AGENT_BROWSER_HEADED), on a
    host with the screen's packages. The screen itself starts with the browser (bot_desktop.auto_start)."""
    try:
        from hermes_cli.config import read_raw_config
        from tools.bot_desktop import runtime

        headed = (read_raw_config().get("browser") or {}).get("headed")
        on = str(headed).strip().lower() in ("true", "1", "yes") if headed is not None else False
        on = on or os.environ.get("AGENT_BROWSER_HEADED", "").strip().lower() in ("true", "1", "yes")
        return on and runtime.is_supported_host() and not runtime.missing_binaries()
    except Exception:
        log.debug("murmur-screen: couldn't tell whether the browser is on the screen", exc_info=True)
        return False


def _shows_the_screen(source: str) -> bool:
    """A chat from this source can show the screen. Unknown counts as yes: the screen check still applies."""
    source = (source or "").strip().lower()
    return not source or source in SHOWN_IN


def _chat_shows_the_screen() -> bool:
    """This turn is a chat in an app that can show the screen. Unknown counts as yes: the screen check still applies."""
    try:
        from gateway.session_context import get_session_env

        source = get_session_env("HERMES_SESSION_SOURCE") or get_session_env("HERMES_SESSION_PLATFORM")
    except Exception:
        return True
    return _shows_the_screen(source)


def _screen_running() -> bool:
    from tools.bot_desktop import runtime

    return bool(runtime.published_env().get("DISPLAY"))


def _human_holds() -> bool:
    from tools.bot_desktop import lease

    return lease.get().holder == lease.HUMAN


def _interrupted() -> bool:
    try:
        from tools.interrupt import is_interrupted

        return is_interrupted()
    except Exception:
        return False


def _keeper(task_id: Optional[str]) -> Callable[[], None]:
    """What keeps everything up while the agent waits: its turn (Hermes's activity heartbeat, which
    clarify uses too), its browser (the janitor closes one idle for browser.inactivity_timeout), and
    the screen (stopped after bot_desktop.idle_stop_minutes)."""
    now = time.monotonic()
    beat = {"last_touch": now, "start": now}

    def keep() -> None:
        try:
            from tools.environments.base import touch_activity_if_due

            touch_activity_if_due(beat, "waiting for the person to take over")
        except Exception:
            pass
        try:
            from tools import browser_tool as bt
            from tools.browser_tool_lifecycle import _update_session_activity

            # The task's own sessions: plain, a served profile's (task@tag) and the local sidecar (::local).
            with bt._cleanup_lock:
                keys = [k for k in bt._session_last_activity if task_id and (k == task_id or k.startswith((f"{task_id}@", f"{task_id}::")))]
            for key in keys:
                _update_session_activity(key)
        except Exception:
            pass
        try:
            from tools.bot_desktop import runtime

            runtime.touch_activity()
        except Exception:
            pass

    return keep


def wait_for_hand_back(
    *,
    human_holds: Callable[[], bool],
    keep: Callable[[], None],
    interrupted: Callable[[], bool],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    wait_s: float = WAIT_S,
) -> dict[str, Any]:
    """Waits for the person to take control and give it back. Pure, so the tests can drive it."""
    start = clock()
    took: Optional[float] = None
    while True:
        if human_holds():
            if took is None:
                took = clock()
        elif took is not None:
            return {"handed_back": True, "seconds": round(clock() - took), "note": TOOK_OVER}
        elif clock() - start >= wait_s:
            return {"handed_back": False, "note": NOBODY}
        if interrupted():
            return {"handed_back": False, "stopped": True, "note": STOPPED}
        keep()
        sleep(TICK_S)


def ask_for_a_hand(args: dict[str, Any], task_id: Optional[str] = None, **kwargs: Any) -> str:
    reason = str(args.get("reason") or "").strip()
    if not reason:
        return _result({"error": "Say what they need to do in `reason`."})
    if not _chat_shows_the_screen():
        return _result({"error": NOT_HERE})
    try:
        if not _screen_running():
            return _result({"error": NO_SCREEN})
    except Exception:
        return _result({"error": NO_SCREEN})
    to_the_front()
    return _result(wait_for_hand_back(human_holds=_human_holds, keep=_keeper(task_id), interrupted=_interrupted))


# ── The agent's tab, in front ───────────────────────────────────────────────────────────────────
# Browser Use opens the agent's tabs behind the one showing (it's made for a person's own browser,
# where it mustn't take the focus) and marks the agent's with 🐴. On the screen the person is there to
# watch the agent, so after each step in its browser the agent's tab comes to the front, and before it
# asks for a hand, so the person takes over the page it means. Through Chrome's DevTools endpoint.

MARK = "\U0001F434 "


def _devtools(path: str) -> Optional[str]:
    """GET /json/<path> from the agent's browser on the screen, or None while it has none."""
    from urllib.request import urlopen

    from tools.bot_desktop import browser

    port = browser.running_instance_cdp_port(str(browser.profile_dir()))
    if not port:
        return None
    with urlopen(f"http://127.0.0.1:{port}/json/{path}", timeout=1) as response:
        return response.read().decode("utf-8", "replace")


def to_the_front(devtools: Callable[[str], Optional[str]] = _devtools) -> bool:
    """Shows the agent's own tab in its browser; False when there's none to show."""
    try:
        tabs = json.loads(devtools("list") or "[]")
        mine = next((t for t in tabs if t.get("type") == "page" and str(t.get("title") or "").startswith(MARK)), None)
        if mine:
            devtools(f"activate/{mine['id']}")
        return bool(mine)
    except Exception:
        log.debug("murmur-screen: couldn't bring the agent's tab to the front", exc_info=True)
        return False


def after_a_step(tool_name: str = "", **_: Any) -> None:
    """After a step in its browser, while the agent has it on the screen: its tab in front."""
    try:
        if tool_name.startswith("browser_") and _screen_running() and not _human_holds():
            to_the_front()
    except Exception:
        log.debug("murmur-screen: couldn't tell whether to show the agent's tab", exc_info=True)


def when_to_ask(session: Any) -> str:
    """The lines a new chat starts with, when its agent can ask for a hand: its browser is on the
    screen and the chat can show it. Otherwise nothing, so other chats don't pay for them."""
    if not _shows_the_screen(str(session.get("platform") or "")) or not _browser_on_screen():
        return ""
    return WHEN_TO_ASK


def _uncached(check: Callable[[], bool]) -> Callable[[], bool]:
    """Hermes caches a tool's availability for 30 seconds; the switch in Murmur should count at once."""
    try:
        from tools.registry import no_cache_check_fn

        return no_cache_check_fn(check)
    except Exception:
        return check


# ── How the browser opens ───────────────────────────────────────────────────────────────────────
# Hermes starts the agent's browser from AGENT_BROWSER_EXECUTABLE_PATH, its setting for running your
# own. The plugin points that at its launcher, which runs the browser it pointed at before: maximized
# on the screen, headless as always off it. Only once there's a browser to run, since pointing the
# setting anywhere earlier stops Hermes installing one, so it tries again as each chat starts (a
# Hermes that installs Chromium on first use gets it from the next chat). Switched off, it's put back.

LAUNCHER = str(Path(__file__).resolve().with_name("chromium"))
BROWSER_PATH = "AGENT_BROWSER_EXECUTABLE_PATH"
RUNS = "MURMUR_SCREEN_BROWSER"  # what the launcher runs
_before: dict[str, Optional[str]] = {}


def _full_browser() -> Optional[str]:
    """The Chromium Hermes opens on the screen (never its windowless headless shell), or None yet."""
    try:
        from tools.bot_desktop.browser import executable

        return executable()
    except Exception:
        return None


def open_through_launcher(environ: MutableMapping[str, str] = os.environ, browser: Callable[[], Optional[str]] = _full_browser) -> None:
    # Already, by this plugin or by another agent's copy of it in the same Hermes.
    if environ.get(RUNS) or not os.access(LAUNCHER, os.X_OK):
        return
    real = browser()
    if real:
        _before["path"] = environ.get(BROWSER_PATH)
        environ[RUNS], environ[BROWSER_PATH] = real, LAUNCHER


def put_back(environ: MutableMapping[str, str] = os.environ) -> None:
    if environ.get(BROWSER_PATH) != LAUNCHER:
        return
    before = _before.pop("path", None)
    if before is None:
        environ.pop(BROWSER_PATH, None)
    else:
        environ[BROWSER_PATH] = before
    environ.pop(RUNS, None)


def register(ctx) -> None:
    ctx.register_tool(name=TOOL, toolset=TOOLSET, schema=SCHEMA, handler=ask_for_a_hand, check_fn=_uncached(_browser_on_screen), emoji="🙋")
    ctx.register_system_prompt_section("murmur-screen", when_to_ask)
    open_through_launcher()
    ctx.register_hook("on_session_start", lambda **_: open_through_launcher())
    ctx.register_hook("post_tool_call", after_a_step)
    ctx.on_unload(put_back)
