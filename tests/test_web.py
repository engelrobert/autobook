"""The browser app: library of books, new book from a folder, settings, AI run, review, pages."""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autobook.ai.client import AIClient
from autobook.catalog import Catalog
from autobook.settings import fotobuch_bin
from autobook.web.server import create_app, list_books, register_book
from tests.conftest import FakeOpenAI

H = {"x-autobook-token": "tok"}


def _app(library, ai_factory=None):
    return TestClient(create_app(library, "tok", ai_factory=ai_factory))


def _wait(c, started):
    jid = started.json()["job"]
    for _ in range(1200):
        j = c.get(f"/api/job/{jid}", headers=H).json()
        if j["status"] != "running":
            assert j["status"] == "done", j.get("error")
            return j["result"]
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_token_cookie_and_global_state(tmp_path):
    c = _app(tmp_path / "lib")
    assert c.get("/api/books").status_code == 403
    assert c.get("/").status_code == 403
    r = c.get("/?t=tok")
    assert r.status_code == 200 and "initial-state" in r.text and "profiles" in r.text
    assert r.text.index("initial-state") < r.text.index("/static/js/main.js")  # state before the app
    g = c.get("/api/books").json()
    assert g["frames"] == ["none", "narrow", "wide"]
    assert {"key": "white", "bg": "#FFFFFF", "ink": "#2D3A45"} in g["swatches"]
    assert c.get("/api/books").status_code == 200  # cookie now set
    for path, media in (("js/main.js", "text/javascript"), ("js/screens/selection.js", "text/javascript"),
                        ("i18n/de.json", "application/json"), ("fonts/newsreader.woff2", "font/woff2"),
                        ("style.css", "text/css"), ("icon.svg", "image/svg+xml")):
        got = c.get(f"/static/{path}")
        assert got.status_code == 200 and got.headers["content-type"].startswith(media), path
    for bad in ("../server.py", "js/../../server.py", "js/a/b/c.js", "index.html", "fonts/OFL-inter.txt"):
        assert c.get(f"/static/{bad}").status_code == 404, bad


def test_library_lists_registered_books_and_rejects_unknown_names(tmp_path, work_copy):
    lib = tmp_path / "lib"
    lib.mkdir()
    register_book(lib, work_copy.root)
    register_book(lib, work_copy.root)  # twice: listed once
    books = list_books(lib)
    assert list(books) == [work_copy.root.name]
    c = _app(lib)
    assert [b["name"] for b in c.get("/api/books", headers=H).json()["books"]] == [work_copy.root.name]
    for bad in ("..", "../x", "nope"):
        assert c.get(f"/api/books/{bad}/state", headers=H).status_code == 404


def test_folder_picker(tmp_path, trip):
    c = _app(tmp_path / "lib")
    roots = c.get("/api/fs", headers=H).json()
    assert roots["roots"] and roots["dirs"] == []
    listing = c.get("/api/fs", params={"path": str(trip[0])}, headers=H).json()
    names = {d["name"] for d in listing["dirs"]}
    assert {"DCIM", "phone"} <= names and listing["parent"]
    assert c.get("/api/fs", params={"path": str(trip[0] / "missing")}, headers=H).status_code == 404
    tile = next(d for d in listing["dirs"] if d["photos"])
    assert tile["previews"] and tile["label"] and not tile["more"]
    img = c.get("/api/fs/preview", params={"path": tile["previews"][0]}, headers=H)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert c.get("/api/fs/preview", params={"path": str(trip[0])}, headers=H).status_code == 404  # not a photo


def test_folder_names_give_dates():
    from autobook.web.server import name_dates

    assert name_dates("2025-08-30 bis 09-06 Urlaub im Jura") == ("2025-08-30", "2025-09-06")
    assert name_dates("2026-05-23 bis 06-04 Urlaub") == ("2026-05-23", "2026-06-04")
    assert name_dates("2026-07 Portugal") == ("2026-07", "2026-07")
    assert name_dates("Ostern") == (None, None) and name_dates("2026-13 nonsense") == (None, None)


def test_new_book_settings_ai_run_review(tmp_path, trip):
    sdk = FakeOpenAI()
    lib = tmp_path / "lib"
    c = _app(lib, ai_factory=lambda work, effort: AIClient(Catalog(work.catalog), sdk_client=sdk, effort=effort))
    started = c.post("/api/books", json={"folder": str(trip[0]), "name": "Portugal/2026"}, headers=H)
    name = started.json()["book"]
    assert name == "Portugal2026"  # no path separators in book names
    summary = _wait(c, started)
    assert summary["photos"] == len(trip[1])
    qs = _wait(c, c.post(f"/api/books/{name}/questions", json={"ui_language": "de"}, headers=H))
    assert {q["maps_to"] for q in qs} >= {"scope", "chaptering"}
    assert next(q for q in qs if q["maps_to"] == "title_tone")["text"] == "Wie sollen die Überschriften klingen?"
    st = c.get(f"/api/books/{name}/state", headers=H).json()
    assert st["questions_meta"] == {"lang": "de", "ai": False} and st["job"] is None
    assert st["suggest"]["highlights"] >= 20 and st["layout_outdated"]
    brief = c.post(f"/api/books/{name}/settings", headers=H, json={
        "answers": {"language": "de"},
        "settings": {"profile": "a4-landscape", "photos": 30, "per_page": 4, "subtitles": True, "ai": True,
                     "effort": "medium"}}).json()
    assert (brief["profile"], brief["target_photos"], brief["photos_per_page"], brief["effort"]) == \
        ("a4-landscape", 30, 4.0, "medium") and brief["subtitles"] and brief["use_ai"]
    est = c.get(f"/api/books/{name}/estimate", headers=H).json()
    assert est["usd"] > 0 and est["minutes"] >= 1 and est["ai"]
    plan = _wait(c, c.post(f"/api/books/{name}/select", headers=H))
    assert plan["selection"] == "ai" and plan["book"]["profile"] == "a4-landscape"
    assert all(call["reasoning"]["effort"] == "medium" for call in sdk.responses.calls)
    state = c.get(f"/api/books/{name}/state", headers=H).json()
    assert state["info"]["state"] == "planned" and state["cost"] > 0
    # review: add an alternative as a minimal record, like the page does; swap via siblings
    ch = plan["chapters"][0]
    alt = ch["alternatives"][0]
    ch["photos"].append({"id": alt, "ts": "", "weight": 1.0})
    assert c.post(f"/api/books/{name}/plan", json=plan, headers=H).status_code == 200
    ch["photos"].append({"id": "p000000000000000", "weight": 1.0})
    assert c.post(f"/api/books/{name}/plan", json=plan, headers=H).status_code == 422
    ch["photos"].pop()
    # page design: frame and colours round-trip, unknown colours are refused
    plan["book"].update(frame="wide", background="sage", page_colors={alt: "anthracite", "chapter:x": "cream"})
    assert c.post(f"/api/books/{name}/plan", json=plan, headers=H).status_code == 200
    book = c.get(f"/api/books/{name}/state", headers=H).json()["plan"]["book"]
    assert (book["frame"], book["background"], book["page_colors"]) == \
        ("wide", "sage", {alt: "anthracite", "chapter:x": "cream"})
    plan["book"]["crops"] = {alt: [0.25, 1]}
    assert c.post(f"/api/books/{name}/plan", json=plan, headers=H).status_code == 200
    assert c.get(f"/api/books/{name}/state", headers=H).json()["plan"]["book"]["crops"] == {alt: [0.25, 1.0]}
    for bad in ({"background": "pink"}, {"page_colors": {alt: "#ff0000"}}, {"frame": "huge"},
                {"crops": {alt: [1.5, 0]}}):
        assert c.post(f"/api/books/{name}/plan", json={**plan, "book": {**plan["book"], **bad}},
                      headers=H).status_code == 422, bad
    sib = c.get(f"/api/books/{name}/siblings/{ch['photos'][0]['id']}", headers=H).json()["siblings"]
    assert isinstance(sib, list)
    assert c.get(f"/thumb/{name}/320/{alt}.jpg", headers=H).status_code == 200
    assert c.get(f"/thumb/{name}/640/{alt}.jpg", headers=H).status_code == 200  # made from the 1024 one
    assert c.get(f"/thumb/{name}/500/{alt}.jpg", headers=H).status_code == 404
    assert c.get(f"/thumb/{name}/320/notanid.jpg", headers=H).status_code == 404
    r = c.post(f"/api/books/{name}/gold", json={"favourites": [alt, "../../etc/passwd"]}, headers=H)
    assert r.json()["count"] == 1
    assert c.get(f"/api/books/{name}/pages", headers=H).json() == {"pages": []}  # not laid out yet


@pytest.mark.skipif(fotobuch_bin() is None, reason="fotobuch binary not installed")
def test_layout_and_page_previews(tmp_path, work_copy):
    lib = tmp_path / "lib"
    lib.mkdir()
    register_book(lib, work_copy.root)
    c = _app(lib)
    name = work_copy.root.name
    c.post(f"/api/books/{name}/settings", json={"answers": {}, "settings": {"ai": False, "photos": 24}}, headers=H)
    _wait(c, c.post(f"/api/books/{name}/select", headers=H))
    r = _wait(c, c.post(f"/api/books/{name}/export", json={"fresh": True}, headers=H))
    assert r["ok"] and r["pages"] >= 24
    pages = c.get(f"/api/books/{name}/pages", headers=H).json()["pages"]
    assert pages[0]["kind"] == "cover" and any(p["kind"] == "opener" for p in pages)
    on_pages = {pid for p in pages for pid in p["photos"]}
    plan = json.loads(work_copy.plan.read_text())
    assert {p["id"] for ch in plan["chapters"] for p in ch["photos"]} <= on_pages
    img = c.get(pages[1]["image"], headers=H)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert c.get(f"/api/books/{name}/pdf", headers=H).status_code == 200
    # where each photo sits on its page, for clicking it in the page view
    photo_pages = [p for p in pages if p["kind"] == "photos"]
    assert all(len(p["rects"]) == len(p["photos"]) for p in photo_pages)
    for r in (r for p in photo_pages for r in p["rects"]):
        assert 0 <= r["x"] < 1 and 0 <= r["y"] < 1 and 0 < r["w"] <= 1 and 0 < r["h"] <= 1
        assert r["x"] + r["w"] <= 1.0001 and r["y"] + r["h"] <= 1.0001
        assert r["ar"] > 0  # the slot's own shape, for choosing the cut
    st = c.get(f"/api/books/{name}/state", headers=H).json()
    assert not st["layout_outdated"] and st["print_outdated"] and st["release"] is None
    # print file: the checks come as codes with values, so the page can word them in any language
    rel = _wait(c, c.post(f"/api/books/{name}/release", headers=H))
    by_id = {ch["id"]: ch for ch in rel["checks"]}
    assert {"pages", "page_size", "fonts", "file_size"} <= set(by_id) and rel["ok"]
    assert by_id["pages"]["values"]["n"] >= 24 and by_id["pages"]["values"]["min"] == 24
    st = c.get(f"/api/books/{name}/state", headers=H).json()
    assert not st["print_outdated"] and st["release"]["ok"] and st["info"]["state"] == "printed"
    # a book laid out by an older autobook is laid out again (even pages, colours)
    typ = Path(json.loads(work_copy.manifest.read_text())["project_dir"]) / "book.typ"
    current = typ.read_text(encoding="utf-8")
    typ.write_text("// an older template\n", encoding="utf-8")
    assert c.get(f"/api/books/{name}/state", headers=H).json()["layout_outdated"]
    typ.write_text(current, encoding="utf-8")
    time.sleep(1.1)  # file times have one-second resolution on some file systems
    plan = json.loads(work_copy.plan.read_text())
    assert c.post(f"/api/books/{name}/plan", json=plan, headers=H).status_code == 200
    st = c.get(f"/api/books/{name}/state", headers=H).json()
    assert st["layout_outdated"] and st["print_outdated"] and st["info"]["state"] == "exported"



@pytest.mark.skipif(fotobuch_bin() is None, reason="fotobuch binary not installed")
def test_photo_count_slider_and_photos_instead_of_blank_pages(tmp_path, work_copy):
    lib = tmp_path / "lib"
    lib.mkdir()
    register_book(lib, work_copy.root)
    c = _app(lib)
    name = work_copy.root.name
    c.post(f"/api/books/{name}/settings", json={"answers": {}, "settings": {"ai": False, "photos": 8}}, headers=H)
    _wait(c, c.post(f"/api/books/{name}/select", headers=H))
    st = c.get(f"/api/books/{name}/state", headers=H).json()
    count = sum(len(ch["photos"]) for ch in st["plan"]["chapters"])
    assert st["capacity"] > count and st["pages_estimate"] > 0 and st["focus"] == {} and 0 < st["least"] <= count
    assert all(f["pages"] >= 24 and f["pages"] % 2 == 0 and f["photos"] <= st["capacity"] for f in st["fit_points"])

    # the slider: nothing is saved, the page applies the plan it gets back
    saved = work_copy.plan.read_text()
    r = c.post(f"/api/books/{name}/resize", json={"target": count + 3}, headers=H).json()
    assert r["count"] == count + 3 and len(r["added"]) == 3 and not r["removed"] and r["pages"] > 0
    assert work_copy.plan.read_text() == saved
    cid = st["plan"]["chapters"][0]["id"]
    r = c.post(f"/api/books/{name}/resize", json={"chapter": cid, "delta": 1}, headers=H).json()
    assert len(r["added"]) == 1 and r["added"][0] in {p["id"] for p in r["plan"]["chapters"][0]["photos"]}
    assert c.post(f"/api/books/{name}/resize", json={"chapter": "nope", "delta": 1}, headers=H).status_code == 422

    # your page split, a page to itself, photos put in or taken out by hand: all saved with the plan
    plan = st["plan"]
    ch = plan["chapters"][0]
    ch["pages"] = [[p["id"] for p in ch["photos"]]]
    ch["excluded"] = ["p_gone"]
    ch["photos"][0].update(layout="page", by_user=True)
    assert c.post(f"/api/books/{name}/plan", json=plan, headers=H).status_code == 200
    back = c.get(f"/api/books/{name}/state", headers=H).json()["plan"]["chapters"][0]
    assert back["pages"] == ch["pages"] and back["excluded"] == ["p_gone"]
    assert back["photos"][0]["layout"] == "page" and back["photos"][0]["by_user"]
    bad = json.loads(json.dumps(plan))
    bad["chapters"][0]["photos"][0]["layout"] = "poster"
    assert c.post(f"/api/books/{name}/plan", json=bad, headers=H).status_code == 422

    # too few photos for the printer: with the switch off blank pages stay, with it on photos come in
    plan["book"]["auto_fit"] = False
    assert c.post(f"/api/books/{name}/plan", json=plan, headers=H).status_code == 200
    r = _wait(c, c.post(f"/api/books/{name}/export", json={"fresh": True}, headers=H))
    assert r["ok"] and r["filled"] == 0 and r["blank_pages"] > 0
    blanks = r["blank_pages"]
    plan["book"]["auto_fit"] = True
    assert c.post(f"/api/books/{name}/plan", json=plan, headers=H).status_code == 200
    r = _wait(c, c.post(f"/api/books/{name}/export", json={}, headers=H))
    assert r["ok"] and r["filled"] > 0 and r["blank_pages"] < blanks and r["pages"] >= 24
    assert sum(len(ch["photos"]) for ch in json.loads(work_copy.plan.read_text())["chapters"]) > count



@pytest.fixture
def memory_keyring(monkeypatch):
    """A keyring that lives in memory (the real one is the computer's password store)."""
    import keyring
    from keyring.backend import KeyringBackend
    from keyring.errors import PasswordDeleteError

    class Memory(KeyringBackend):
        priority = 1

        def __init__(self):
            super().__init__()
            self.store = {}

        def get_password(self, service, user):
            return self.store.get((service, user))

        def set_password(self, service, user, password):
            self.store[(service, user)] = password

        def delete_password(self, service, user):
            if self.store.pop((service, user), None) is None:
                raise PasswordDeleteError("none")

    before = keyring.get_keyring()
    mem = Memory()
    keyring.set_keyring(mem)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    yield mem
    keyring.set_keyring(before)


def test_the_openai_key_is_entered_in_the_page(tmp_path, memory_keyring):
    from autobook.cli import _ui_ai_factory

    checked = []

    def check(key):
        checked.append(key)
        return "invalid" if key.endswith("bad") else "ok"

    c = TestClient(create_app(tmp_path / "lib", "tok", ai_factory=_ui_ai_factory(1.0, "gpt-6-luna"), key_check=check))
    assert c.get("/api/key").status_code == 403
    assert c.get("/api/key", headers=H).json() == {"set": False, "from_env": False, "ai_available": False}
    assert c.get("/api/books", headers=H).json()["ai_available"] is False
    assert c.post("/api/key", json={"key": "hello"}, headers=H).status_code == 422 and not checked
    good, bad = "sk-proj-" + "x" * 40, "sk-proj-" + "y" * 40 + "bad"
    assert c.post("/api/key", json={"key": bad}, headers=H).status_code == 422 and not memory_keyring.store
    r = c.post("/api/key", json={"key": f"  {good} "}, headers=H).json()
    assert r == {"ok": True, "checked": True, "ai_available": True} and checked[-1] == good
    assert list(memory_keyring.store.values()) == [good]
    assert c.get("/api/books", headers=H).json()["ai_available"] is True  # at once, no restart
    assert good not in c.get("/api/key", headers=H).text  # the key itself never goes back to the page
    assert c.delete("/api/key", headers=H).json() == {"ok": True, "ai_available": False}
    assert not memory_keyring.store


def test_updates_are_offered_and_wait_until_autobook_is_idle(tmp_path):
    from types import SimpleNamespace

    from autobook.update import Updater

    class Manager:
        downloads = 0

        def check_for_updates(self):
            return SimpleNamespace(TargetFullRelease=SimpleNamespace(Version="9.1.0"))

        def download_updates(self, info, progress):
            Manager.downloads += 1
            progress(50)
            time.sleep(0.5)  # long enough to try a second update meanwhile

    restarted = []
    up = Updater(manager=Manager(), restart=restarted.append)
    c = TestClient(create_app(tmp_path / "lib", "tok", updater=up))
    g = c.get("/api/books", headers=H).json()
    assert g["updates"] is True and g["version"]
    st = c.get("/api/update?check=true", headers=H).json()
    assert st["available"] and st["version"] == "9.1.0" and st["enabled"]
    started = c.post("/api/update/apply", headers=H)
    assert c.post("/api/update/apply", headers=H).status_code == 409  # busy with the first one
    assert _wait(c, started) == {"restarting": True, "version": "9.1.0"}
    time.sleep(2)  # the restart comes after the page got its answer
    assert len(restarted) == 1 and Manager.downloads == 1

    # without an update feed (development, Linux): nothing is offered
    c = TestClient(create_app(tmp_path / "lib", "tok", updater=Updater()))
    assert c.get("/api/books", headers=H).json()["updates"] is False
    assert c.get("/api/update?check=true", headers=H).json()["available"] is False
    assert c.post("/api/update/apply", headers=H).status_code == 409


def test_every_text_of_the_app_exists_in_every_language():
    import re
    from pathlib import Path

    static = Path(__file__).parent.parent / "src/autobook/web/static"
    langs = {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in (static / "i18n").glob("*.json")}
    assert {"de", "en"} <= set(langs)
    for code, d in langs.items():
        assert set(d) == set(langs["en"]), (code, set(d) ^ set(langs["en"]))
    js = "\n".join(f.read_text(encoding="utf-8") for f in (static / "js").rglob("*.js"))
    used = set(re.findall(r'\bt\(\s*"([\w.-]+)"', js)) | set(re.findall(r'"([a-z]+\.[\w.]+)"', js)) & set(langs["en"])
    missing = {k for k in used if k not in langs["en"]}
    assert not missing, missing
    for prefix in re.findall(r"t\(`([\w.]+)\.\$\{", js):  # t(`format.${name}`) etc.
        assert any(k.startswith(prefix + ".") for k in langs["en"]), prefix
    for p in ("a4-landscape", "a4-portrait", "square-21", "square-30"):
        assert f"format.{p}" in langs["en"]
