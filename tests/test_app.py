"""The desktop app around the server (autobook/app.py): one instance, a fixed address, the tray menu."""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

from autobook import app
from autobook.update import Updater
from autobook.web.server import free_port, make_server


def test_the_address_stays_the_same(tmp_path):
    port, token = app.address(tmp_path, preferred=free_port())
    assert app.address(tmp_path, preferred=1) == (port, token)  # kept, not the new preference
    data = json.loads((tmp_path / "address.json").read_text())
    assert data == {"port": port, "token": token}


def test_a_second_start_finds_the_running_one(tmp_path):
    assert app.running(tmp_path) is None
    port = free_port()
    server = make_server(tmp_path / "lib", "tok", port)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        url = f"http://127.0.0.1:{port}/?t=tok"
        (tmp_path / "instance.json").write_text(json.dumps({"pid": 1, "url": url}))
        assert app.running(tmp_path) == url
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert app.running(tmp_path) is None  # a file left behind by a crash does not count


class FakeTray:
    """Enough of pystray to build the menu and press its items."""

    def __init__(self):
        self.icon = None
        self.notes = []

    def MenuItem(self, text, action, default=False):  # noqa: N802 - pystray's names
        return SimpleNamespace(text=text, action=action, default=default)

    def Menu(self, *items):  # noqa: N802
        return list(items)

    def Icon(self, name, image, title, menu):  # noqa: N802
        fake = self

        class Icon:
            def __init__(self):
                self.menu, self.stopped = menu, False
                fake.icon = self

            def run(self):
                pass

            def stop(self):
                self.stopped = True

            def notify(self, text, title):
                fake.notes.append(text)
        return Icon()


class FakeManager:
    def __init__(self, version=None):
        self.version = version

    def check_for_updates(self):
        if not self.version:
            return None
        return SimpleNamespace(TargetFullRelease=SimpleNamespace(Version=self.version))


def test_tray_menu_opens_checks_and_quits(monkeypatch):
    opened = []
    monkeypatch.setattr(app.webbrowser, "open", opened.append)
    monkeypatch.setattr(app, "lang", lambda: "de")
    server = SimpleNamespace(should_exit=False)
    tray = FakeTray()
    app.tray("http://127.0.0.1:1/?t=x", server, Updater(manager=FakeManager()), pystray=tray)
    items = {i.text: i for i in tray.icon.menu}
    assert list(items) == ["autobook öffnen", "Nach Updates suchen", "Beenden"] and items["autobook öffnen"].default
    items["autobook öffnen"].action(tray.icon, None)
    assert opened == ["http://127.0.0.1:1/?t=x"]
    items["Nach Updates suchen"].action(tray.icon, None)
    assert tray.notes == ["autobook ist auf dem neuesten Stand."]
    items["Beenden"].action(tray.icon, None)
    assert server.should_exit and tray.icon.stopped

    # with a newer version: a note, and the page (which shows the update notice) opens
    tray = FakeTray()
    app.tray("u", server, Updater(manager=FakeManager("9.9.9")), pystray=tray)
    {i.text: i for i in tray.icon.menu}["Nach Updates suchen"].action(tray.icon, None)
    assert "9.9.9" in tray.notes[0] and opened[-1] == "u"
    # no update feed (development, Linux): no update item at all
    tray = FakeTray()
    app.tray("u", server, Updater(), pystray=tray)
    assert [i.text for i in tray.icon.menu] == ["autobook öffnen", "Beenden"]


def test_the_installed_app_finds_its_own_fotobuch(tmp_path, monkeypatch):
    import os
    import sys

    from autobook.settings import fotobuch_bin

    exe = tmp_path / ("fotobuch.exe" if os.name == "nt" else "fotobuch")
    (tmp_path / "fotobuch").mkdir()
    (tmp_path / "fotobuch" / exe.name).write_text("")
    monkeypatch.delenv("FOTOBUCH_BIN", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "autobook.exe"))
    assert fotobuch_bin() == str(tmp_path / "fotobuch" / exe.name)


def test_the_app_starts_without_a_console(tmp_path, monkeypatch):
    """autobook.exe has no console window: sys.stdout and sys.stderr are None. uvicorn's own console
    logging asked stdout.isatty() and the first start on Windows failed."""
    import sys
    import urllib.request

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    port = free_port()
    server = make_server(tmp_path / "lib", "tok", port, console=False)  # no streams needed at all
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=5) as r:
            assert json.load(r)["app"] == "autobook"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    app.console_streams()  # and anything else that writes to them gets somewhere to go
    assert sys.stdout.write("x") == 1 and sys.stderr.isatty() in (True, False)  # "nul" is a tty on Windows
    make_server(tmp_path / "lib", "tok", free_port())  # even uvicorn's console logging works then


def test_fotobuch_runs_without_a_console_window(tmp_path, monkeypatch):
    """On Windows every Fotobuch call from the windowed app opened a console window."""
    import subprocess
    import sys

    from autobook.export.fotobuch import Fotobuch

    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return SimpleNamespace(returncode=0, stdout="fotobuch 0.3.0", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "platform", "linux")
    Fotobuch(binary="fotobuch").run(["--version"], tmp_path)
    assert "creationflags" not in seen  # elsewhere nothing changes
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    Fotobuch(binary="fotobuch.exe").run(["--version"], tmp_path)
    assert seen["creationflags"] == 0x08000000


class BrokenManager:
    def check_for_updates(self):
        raise OSError("feed not reachable")


def test_a_failed_update_check_says_so(monkeypatch):
    """0.2.1's tray said "up to date" when the check had failed; the reason was only in the log."""
    monkeypatch.setattr(app, "lang", lambda: "de")
    up = Updater(manager=BrokenManager())
    assert up.status()["checked"] is False
    st = up.check()
    assert st["checked"] and not st["available"] and st["error"] == "OSError: feed not reachable"
    tray = FakeTray()
    app.tray("u", SimpleNamespace(should_exit=False), up, pystray=tray)
    {i.text: i for i in tray.icon.menu}["Nach Updates suchen"].action(tray.icon, None)
    assert tray.notes == ["Die Suche nach Updates hat nicht geklappt: OSError: feed not reachable"]
    ok = Updater(manager=FakeManager("9.9.9")).check()  # a later good check clears the error
    assert ok["checked"] and ok["available"] and ok["error"] is None and ok["version"] == "9.9.9"


def test_the_feed_ends_with_a_slash_and_failures_set_the_exit_code(monkeypatch, capsys):
    """0.2.1 and 0.2.2 asked GitHub for .../latest/releases.win.json (404): Velopack joins the file
    name onto the feed like a URL. And `autobook update-check` exited 0 even when the check failed."""
    import sys

    import pytest

    import autobook.update as upd
    from autobook import cli

    monkeypatch.setitem(sys.modules, "autobook._build",
                        SimpleNamespace(UPDATE_FEED="https://github.com/x/autobook/releases/latest/download"))
    assert upd.feed_url() == "https://github.com/x/autobook/releases/latest/download/"
    monkeypatch.setattr(upd.Updater, "check", lambda self: {"error": "RuntimeError: Http error: 404"})
    monkeypatch.setattr(sys, "argv", ["autobook", "update-check"])
    with pytest.raises(SystemExit) as e:
        cli.main()
    assert e.value.code == 1 and "404" in capsys.readouterr().out
