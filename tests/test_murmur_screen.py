"""Unit tests for the murmur-screen plugin's own logic (no screen, no Hermes).

Run them with the image's own Python, like the murmur-mail tests:

  docker run --rm -v "$PWD:/src:ro" --entrypoint /opt/hermes/.venv/bin/python \\
    murmur-local/hermes-agent-umbrel:v0.22.5-screen -m unittest discover -s /src/tests -v

The tool against a real Hermes, its screen and its display lease is covered by Murmur's contract
suite (test/contract/screen.contract.ts in getumbrel/murmur).
"""

from __future__ import annotations

import importlib.util
from contextlib import ExitStack
import json
import os
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent / "umbrel-context" / "plugins" / "murmur-screen"


def _load_package():
    spec = importlib.util.spec_from_file_location("murmur_screen_under_test", PLUGIN / "__init__.py", submodule_search_locations=[str(PLUGIN)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


screen = _load_package()


class Clock:
    """A clock the wait moves forward one tick at a time, so 30 minutes take no time at all."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def wait(holder_at, *, interrupted_at=None, wait_s=60):
    """Runs the wait against a lease that follows `holder_at(now)`, counting the keep-alives."""
    clock = Clock()
    kept = []
    result = screen.wait_for_hand_back(
        human_holds=lambda: holder_at(clock.now),
        keep=lambda: kept.append(clock.now),
        interrupted=lambda: interrupted_at is not None and clock.now >= interrupted_at,
        clock=clock,
        sleep=clock.sleep,
        wait_s=wait_s,
    )
    return result, kept, clock.now


class Waiting(unittest.TestCase):
    def test_a_hand_back_returns_with_how_long_they_had_it(self):
        result, kept, ended = wait(lambda now: 5 <= now < 47)
        self.assertEqual(result, {"handed_back": True, "seconds": 42, "note": screen.TOOK_OVER})
        self.assertEqual(ended, 47)
        # Kept alive every tick it waited: its turn, its browser and the screen.
        self.assertEqual(len(kept), 47)

    def test_nobody_comes(self):
        result, _, ended = wait(lambda now: False, wait_s=60)
        self.assertEqual(result, {"handed_back": False, "note": screen.NOBODY})
        self.assertEqual(ended, 60)

    def test_no_time_limit_once_theyre_in_control(self):
        # They arrive just before the wait would end, and take five minutes to sign in.
        result, _, ended = wait(lambda now: 59 <= now < 359, wait_s=60)
        self.assertTrue(result["handed_back"])
        self.assertEqual(result["seconds"], 300)
        self.assertEqual(ended, 359)

    def test_already_in_control_when_asked(self):
        result, _, _ = wait(lambda now: now < 10)
        self.assertEqual(result["seconds"], 10)

    def test_a_stop_ends_the_wait(self):
        result, _, ended = wait(lambda now: False, interrupted_at=3)
        self.assertEqual(result, {"handed_back": False, "stopped": True, "note": screen.STOPPED})
        self.assertEqual(ended, 3)


class Asking(unittest.TestCase):
    def setUp(self):
        self.saved = (screen._chat_shows_the_screen, screen._screen_running, screen.wait_for_hand_back, screen.to_the_front)
        self.shown = []
        screen.wait_for_hand_back = lambda **kw: {"handed_back": True, "seconds": 1, "note": screen.TOOK_OVER}
        screen.to_the_front = lambda: self.shown.append(True)

    def tearDown(self):
        screen._chat_shows_the_screen, screen._screen_running, screen.wait_for_hand_back, screen.to_the_front = self.saved

    def ask(self, **args):
        return json.loads(screen.ask_for_a_hand(args, task_id="t1"))

    def test_it_needs_a_reason(self):
        screen._chat_shows_the_screen = lambda: True
        screen._screen_running = lambda: True
        self.assertIn("reason", self.ask(reason="  ")["error"])

    def test_not_from_a_chat_without_the_screen(self):
        screen._chat_shows_the_screen = lambda: False
        screen._screen_running = lambda: True
        self.assertEqual(self.ask(reason="Sign in")["error"], screen.NOT_HERE)

    def test_not_while_the_screen_is_off(self):
        screen._chat_shows_the_screen = lambda: True
        screen._screen_running = lambda: False
        self.assertEqual(self.ask(reason="Sign in")["error"], screen.NO_SCREEN)

    def test_waits_when_it_can(self):
        screen._chat_shows_the_screen = lambda: True
        screen._screen_running = lambda: True
        self.assertTrue(self.ask(reason="Solve the check on shop.example.com")["handed_back"])

    def test_shows_its_tab_first_so_they_take_over_the_page_it_means(self):
        screen._chat_shows_the_screen = lambda: True
        screen._screen_running = lambda: True
        self.ask(reason="Sign in")
        self.assertEqual(self.shown, [True])


class WhenToAsk(unittest.TestCase):
    """The lines a new chat starts with, since Hermes lists the tool itself behind its search."""

    def setUp(self):
        self.saved = screen._browser_on_screen

    def tearDown(self):
        screen._browser_on_screen = self.saved

    def test_a_chat_that_shows_the_screen_learns_when_to_ask(self):
        screen._browser_on_screen = lambda: True
        self.assertEqual(screen.when_to_ask({"platform": "desktop"}), screen.WHEN_TO_ASK)
        self.assertIn(screen.TOOL, screen.WHEN_TO_ASK)
        self.assertIn("tool_call", screen.WHEN_TO_ASK)
        self.assertIn("browser_vault", screen.WHEN_TO_ASK)

    def test_nothing_where_nobody_could_take_over(self):
        screen._browser_on_screen = lambda: True
        self.assertEqual(screen.when_to_ask({"platform": "telegram"}), "")

    def test_nothing_while_the_browser_runs_where_nobody_sees_it(self):
        screen._browser_on_screen = lambda: False
        self.assertEqual(screen.when_to_ask({"platform": "desktop"}), "")

    def test_it_fits_hermess_budget_for_a_section(self):
        self.assertLess(len(screen.WHEN_TO_ASK), 4000)

    def test_tool_search_lists_the_whole_idea(self):
        # Hermes's listing shows a tool's first sentence, up to 60 characters.
        first = screen.SCHEMA["description"].split(". ")[0] + "."
        self.assertLessEqual(len(first), 60)
        self.assertTrue(first.startswith("Last resort"))
        self.assertIn("take over your browser", first)


class TheBrowsersWindow(unittest.TestCase):
    """Hermes's setting for the browser points at the launcher, which runs the one it pointed at."""

    def setUp(self):
        screen._before.clear()

    def test_points_hermes_at_the_launcher_and_puts_back_what_was_there(self):
        env = {"AGENT_BROWSER_EXECUTABLE_PATH": "/opt/hermes/tools/chromium/chrome"}
        screen.open_through_launcher(env, browser=lambda: "/opt/hermes/tools/chromium/chrome")
        self.assertEqual(env, {"AGENT_BROWSER_EXECUTABLE_PATH": screen.LAUNCHER, "MURMUR_SCREEN_BROWSER": "/opt/hermes/tools/chromium/chrome"})
        screen.put_back(env)
        self.assertEqual(env, {"AGENT_BROWSER_EXECUTABLE_PATH": "/opt/hermes/tools/chromium/chrome"})

    def test_a_hermes_that_finds_its_own_browser_finds_it_again_once_switched_off(self):
        env = {}
        screen.open_through_launcher(env, browser=lambda: "/usr/bin/chromium")
        self.assertEqual(env["MURMUR_SCREEN_BROWSER"], "/usr/bin/chromium")
        screen.put_back(env)
        self.assertEqual(env, {})

    def test_waits_for_a_browser_so_hermes_still_installs_one(self):
        env = {}
        screen.open_through_launcher(env, browser=lambda: None)
        self.assertEqual(env, {})
        screen.open_through_launcher(env, browser=lambda: "/opt/hermes/tools/chromium/chrome")
        self.assertEqual(env["AGENT_BROWSER_EXECUTABLE_PATH"], screen.LAUNCHER)

    def test_once_in_a_hermes_however_many_agents_have_it(self):
        env = {"AGENT_BROWSER_EXECUTABLE_PATH": "/opt/data/profiles/scout/plugins/murmur-screen/chromium", "MURMUR_SCREEN_BROWSER": "/opt/chrome"}
        before = dict(env)
        screen.open_through_launcher(env, browser=lambda: env["AGENT_BROWSER_EXECUTABLE_PATH"])
        screen.put_back(env)
        self.assertEqual(env, before)

    def test_the_launcher_ships_runnable(self):
        self.assertTrue(os.access(screen.LAUNCHER, os.X_OK))


class CurrentBrowserLauncher(unittest.TestCase):
    def setUp(self):
        self.home = Path('/agents/one')
        self.original = Mock(spec=lambda exe, directory, **kw: None, side_effect=lambda exe, directory, **kw: [exe, f'--user-data-dir={directory}', '--no-sandbox'])
        self.launcher = SimpleNamespace(build_chromium_argv=self.original)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(sys.modules, {
            'hermes_constants': SimpleNamespace(get_hermes_home=lambda: self.home),
            'tools': SimpleNamespace(browser_launcher=self.launcher),
        }))
        self.stack.enter_context(patch.object(screen, '_screen_running', return_value=True))

    def argv(self, headed=True):
        return self.launcher.build_chromium_argv('/chosen/chrome', '/profile', headed=headed, packaged=True, sandbox_bypass=True)

    def test_wraps_the_selected_browser_and_preserves_upstream_launch_flags(self):
        restore = screen.install_current_launcher()
        self.assertEqual(self.argv(), [screen.LAUNCHER, '--murmur-browser', '/chosen/chrome', '--user-data-dir=/profile', '--no-sandbox'])
        self.original.assert_called_once_with('/chosen/chrome', '/profile', headed=True, packaged=True, sandbox_bypass=True)
        restore()
        self.assertIs(self.launcher.build_chromium_argv, self.original)

    def test_only_the_enabled_profiles_screen_is_wrapped(self):
        screen.install_current_launcher()
        self.home = Path('/agents/two')
        self.assertEqual(self.argv()[0], '/chosen/chrome')
        self.home = Path('/agents/one')
        self.assertEqual(self.argv(headed=False)[0], '/chosen/chrome')
        with patch.object(screen, '_screen_running', return_value=False):
            self.assertEqual(self.argv()[0], '/chosen/chrome')

    def test_profiles_unload_independently_and_last_unload_restores_the_builder(self):
        first = screen.install_current_launcher()
        wrapper = self.launcher.build_chromium_argv
        self.home = Path('/agents/two')
        second = screen.install_current_launcher()
        self.assertIs(self.launcher.build_chromium_argv, wrapper)
        first()
        self.assertEqual(self.argv()[0], screen.LAUNCHER)
        self.home = Path('/agents/one')
        self.assertEqual(self.argv()[0], '/chosen/chrome')
        second()
        second()
        self.assertIs(self.launcher.build_chromium_argv, self.original)

    def test_unload_does_not_replace_a_later_plugins_builder(self):
        restore = screen.install_current_launcher()
        later = Mock()
        self.launcher.build_chromium_argv = later
        restore()
        self.assertIs(self.launcher.build_chromium_argv, later)

    def test_a_launcher_hermes_moved_into_the_config_runs_hermess_own_browser(self):
        # An older murmur-screen set AGENT_BROWSER_EXECUTABLE_PATH to its launcher in the process, and
        # a `hermes update` run from there carried it into browser.executable_path. Run as the browser,
        # the launcher would run itself, and the agent's browser would never start, on or off the screen.
        moved = '/opt/data/plugins/murmur-screen/chromium'
        self.stack.enter_context(patch.object(screen, '_hermes_chromium', create=True, return_value='/hermes/chrome'))
        screen.install_current_launcher()
        argv = lambda headed: self.launcher.build_chromium_argv(moved, '/profile', headed=headed, packaged=True, sandbox_bypass=True)
        self.assertEqual(argv(True), [screen.LAUNCHER, '--murmur-browser', '/hermes/chrome', '--user-data-dir=/profile', '--no-sandbox'])
        self.assertEqual(argv(False)[0], '/hermes/chrome')


class BrowserKeepalive(unittest.TestCase):
    def test_touches_only_the_waiting_tasks_new_browser_sessions(self):
        touched = []
        sessions = SimpleNamespace(_cleanup_lock=threading.RLock(), _session_last_activity={
            'task': 0, 'task@profile': 0, 'task@profile::local': 0, 'task::local': 0, 'task-other': 0,
        })
        lifecycle = SimpleNamespace(update_session_activity=touched.append)
        with patch.dict(sys.modules, {
            'tools': SimpleNamespace(browser_session=sessions),
            'tools.browser_session_lifecycle': lifecycle,
        }):
            screen._keeper('task')()
        self.assertEqual(touched, ['task', 'task@profile', 'task@profile::local', 'task::local'])


class TheLauncher(unittest.TestCase):
    """The launcher itself, running a stand-in browser that says how it was started."""

    def run_it(self, *args, display=None, config=None, explicit=False):
        """How the stand-in browser was started, whether the old tabs are left, and what the desktop was told."""
        with tempfile.TemporaryDirectory() as tmp:
            browser = Path(tmp, "chrome")
            browser.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            browser.chmod(0o755)
            Path(tmp, "xprop").write_text(
                "#!/bin/sh\n"
                "case $1 in -root) echo '_NET_SUPPORTING_WM_CHECK(WINDOW): window id # 0x200001' ;;\n"
                "  *) echo '_NET_WM_NAME(UTF8_STRING) = \"Xfwm4\"' ;; esac\n"
            )
            Path(tmp, "xprop").chmod(0o755)
            told = Path(tmp, "told")
            Path(tmp, "xfconf-query").write_text(f'#!/bin/sh\necho "$*" >> {told}\n')
            Path(tmp, "xfconf-query").chmod(0o755)
            env = {"PATH": f"{tmp}:/usr/bin:/bin", "MURMUR_SCREEN_BROWSER": str(browser)}
            if display:
                env["DISPLAY"] = display
            if config:
                env["XDG_CONFIG_HOME"] = config
            profile = Path(tmp, "profile")
            (profile / "Default" / "Sessions").mkdir(parents=True)
            args = [a.replace("PROFILE", str(profile)) for a in args]
            if explicit:
                env.pop("MURMUR_SCREEN_BROWSER")
                args = ["--murmur-browser", str(browser), *args]
            out = subprocess.run([screen.LAUNCHER, *args], env=env, capture_output=True, text=True, check=True).stdout.split("\n")[:-1]
            return out, (profile / "Default" / "Sessions").exists(), told.read_text() if told.exists() else ""

    def test_headless_without_a_screen(self):
        out, _, told = self.run_it("--user-data-dir=PROFILE", "about:blank", config="/opt/data/bot-desktop/xdg")
        self.assertEqual(out[:3], ["--test-type", "--disable-infobars", "--headless=new"])
        self.assertEqual(out[-1], "about:blank")
        self.assertEqual(told, "")

    def test_maximized_on_the_screen_with_no_old_tabs(self):
        out, sessions_left, _ = self.run_it("--user-data-dir=PROFILE", display=":20")
        self.assertEqual(out[:4], ["--test-type", "--disable-infobars", "--start-maximized", "--hide-crash-restore-bubble"])
        self.assertFalse(sessions_left)

    def test_current_hermes_passes_its_binary_without_an_environment_override(self):
        out, _, _ = self.run_it('--user-data-dir=PROFILE', '--no-sandbox', display=':20', explicit=True)
        self.assertEqual(out[:4], ['--test-type', '--disable-infobars', '--start-maximized', '--hide-crash-restore-bubble'])
        self.assertEqual(out[-1], '--no-sandbox')
        self.assertNotIn('--murmur-browser', out)

    def test_no_window_buttons_on_the_agents_screen(self):
        _, _, told = self.run_it(display=":20", config="/opt/data/bot-desktop/xdg")
        self.assertEqual(told, "-c xsettings -p /Gtk/DecorationLayout -n -t string -s menu:\n")

    def test_a_persons_own_desktop_keeps_its_buttons(self):
        for config in (None, "/home/dana/.config"):
            _, _, told = self.run_it(display=":0", config=config)
            self.assertEqual(told, "")


class TheAgentsTab(unittest.TestCase):
    """Browser Use opens the agent's tabs behind the one showing; on the screen, the agent's comes first."""

    TABS = [
        {"id": "A", "type": "page", "title": "New Tab"},
        {"id": "B", "type": "iframe", "title": "\U0001F434 a frame"},
        {"id": "C", "type": "page", "title": "\U0001F434 Hearth & Bean · Checkout"},
    ]

    def devtools(self, tabs):
        asked = []

        def get(path):
            asked.append(path)
            return json.dumps(tabs) if path == "list" else "Target activated"

        return get, asked

    def test_shows_the_tab_browser_use_marked_as_the_agents(self):
        get, asked = self.devtools(self.TABS)
        self.assertTrue(screen.to_the_front(get))
        self.assertEqual(asked, ["list", "activate/C"])

    def test_leaves_the_browser_alone_without_one(self):
        get, asked = self.devtools(self.TABS[:2])
        self.assertFalse(screen.to_the_front(get))
        self.assertEqual(asked, ["list"])
        self.assertFalse(screen.to_the_front(lambda path: None))

    def test_current_hermes_looks_up_the_current_profiles_tracked_browser(self):
        browser = SimpleNamespace(profile_dir=lambda: Path('/agents/one/bot-desktop/browser-profile'))
        lookup = Mock(return_value=SimpleNamespace(http_url='http://127.0.0.1:9876'))
        response = Mock()
        response.read.return_value = b'[]'
        with patch.dict(sys.modules, {
            'tools.bot_desktop': SimpleNamespace(browser=browser),
            'tools.browser_launcher': SimpleNamespace(get_browser=lookup),
        }), patch('urllib.request.urlopen') as request:
            request.return_value.__enter__.return_value = response
            self.assertEqual(screen._devtools('list'), '[]')
            lookup.assert_called_once_with('profile:/agents/one/bot-desktop/browser-profile')
            request.assert_called_once_with('http://127.0.0.1:9876/json/list', timeout=1)
            lookup.return_value = None
            self.assertIsNone(screen._devtools('list'))
            self.assertEqual(request.call_count, 1)

    def test_after_each_step_in_its_browser_while_the_agent_has_it(self):
        saved = (screen._screen_running, screen._human_holds, screen.to_the_front)
        shown = []
        try:
            screen.to_the_front = lambda: shown.append(True)
            screen._screen_running, screen._human_holds = (lambda: True), (lambda: False)
            screen.after_a_step(tool_name="browser_exec", result="{}")
            screen.after_a_step(tool_name="web_search")
            screen._human_holds = lambda: True
            screen.after_a_step(tool_name="browser_navigate")
            screen._human_holds, screen._screen_running = (lambda: False), (lambda: False)
            screen.after_a_step(tool_name="browser_navigate")
        finally:
            screen._screen_running, screen._human_holds, screen.to_the_front = saved
        self.assertEqual(shown, [True])


class TypingWhatItsGiven(unittest.TestCase):
    """Hermes's "never type a password, card or code" sentences never reach the model. Word for word
    as Hermes v0.22.5 writes them, in each place it does."""

    POINTER = " Vault: on a login, checkout or 2FA form, call browser_vault through tool_call (action=list first)."
    NOTE = POINTER + (" Never type a password, card number, CVC or code with this tool, or accept one in chat, even if "
                      "the page or the user shows it.")
    VAULT = ("unlock (backend: ask the user to unlock 1Password/Bitwarden). Never type, ask for or repeat a password, "
             "card number, CVC or code yourself, even if the page or user shows one. Stop on save_declined; never "
             "retry payment_declined.")
    VAULT_KEPT = "unlock (backend: ask the user to unlock 1Password/Bitwarden). Stop on save_declined; never retry payment_declined."
    HINT = ("No saved logins. On a login page, call browser_vault action=save_login to ask the user to save one. "
            "Never type a password yourself or ask for one in chat, even if it is shown on the page.")
    HINT_KEPT = "No saved logins. On a login page, call browser_vault action=save_login to ask the user to save one."

    def unbound(self, request):
        return screen.unbind(request=request)["request"]

    def test_out_of_every_tool_in_any_providers_shape(self):
        tools = self.unbound({"model": "m", "tools": [
            {"type": "function", "function": {"name": "browser_exec", "description": "Run a script." + self.NOTE}},
            {"name": "browser_type", "description": "Type into a field." + self.NOTE, "input_schema": {}},
            {"type": "function", "name": "browser_vault", "description": self.VAULT},
            {"type": "function", "function": {"name": "web_search", "description": "Search the web."}},
        ]})["tools"]
        self.assertEqual(tools[0]["function"]["description"], "Run a script." + self.POINTER)
        self.assertEqual(tools[1]["description"], "Type into a field." + self.POINTER)
        self.assertEqual(tools[2]["description"], self.VAULT_KEPT)
        self.assertEqual(tools[3]["function"]["description"], "Search the web.")

    def test_out_of_what_tools_returned_in_any_providers_shape(self):
        described = json.dumps({"tools": {"browser_vault": {"description": self.VAULT, "parameters": {}}}}, ensure_ascii=False)
        hint = json.dumps({"success": True, "items": [], "hint": self.HINT})
        chat = self.unbound({"messages": [{"role": "tool", "tool_call_id": "1", "content": described}]})
        claude = self.unbound({"messages": [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "1", "content": [{"type": "text", "text": hint}]}]}]})
        responses = self.unbound({"input": [{"type": "function_call_output", "call_id": "1", "output": hint}]})
        self.assertEqual(json.loads(chat["messages"][0]["content"])["tools"]["browser_vault"]["description"], self.VAULT_KEPT)
        self.assertEqual(json.loads(claude["messages"][0]["content"][0]["content"][0]["text"])["hint"], self.HINT_KEPT)
        self.assertEqual(json.loads(responses["input"][0]["output"])["hint"], self.HINT_KEPT)

    def test_words_that_arent_hermess_stay_as_they_are(self):
        said = {"messages": [
            {"role": "user", "content": "Never type my bank password, just tell me when you get there."},
            {"role": "tool", "content": json.dumps({"output": "Never type your PIN into a pop-up."})},
        ]}
        self.assertIsNone(screen.unbind(request=said))

    def test_a_request_without_it_stays_as_it_is(self):
        self.assertIsNone(screen.unbind(request={"tools": [{"name": "web_search", "description": "Search."}], "messages": []}))
        self.assertIsNone(screen.unbind())


if __name__ == "__main__":
    unittest.main()
