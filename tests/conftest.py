from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from autobook.settings import Work, fotobuch_bin
from autobook.synth import generate_trip

SMALL_STOPS = [
    ("Lisboa", 38.7139, -9.1394, 0, 22),
    ("Porto", 41.1496, -8.6110, 3, 22),
    ("Lagos", 37.1028, -8.6730, 7, 18),
]


@pytest.fixture(scope="session")
def trip(tmp_path_factory):
    root = tmp_path_factory.mktemp("trip") / "2026-07 Portugal Rundreise"  # a folder name people would use
    photos = generate_trip(root, stops=SMALL_STOPS, long_edge=1200, heic_every=9, seed=3)
    return root, photos


@pytest.fixture(scope="session")
def scanned(trip, tmp_path_factory) -> Work:
    from autobook.pipeline.scan import run_scan

    work = Work(tmp_path_factory.mktemp("work"))
    run_scan([trip[0]], work)
    return work


@pytest.fixture
def work_copy(scanned, tmp_path) -> Work:
    """A private copy of the scanned work dir (tests may change it)."""
    dst = tmp_path / "work"
    shutil.copytree(scanned.root, dst)
    return Work(dst)


requires_fotobuch = pytest.mark.skipif(fotobuch_bin() is None, reason="fotobuch binary not installed")


# ── fake OpenAI client ───────────────────────────────────────────────────────
ALIAS = re.compile(r"\[id=(p\d\d)\]")


def response_body(data: dict | None, *, status: str = "completed", refusal: str | None = None,
                  in_tok: int = 1000, cached: int = 0, out_tok: int = 900, reasoning: int = 800) -> dict:
    """A Responses API object as `model_dump()` returns it (reasoning item + message)."""
    content = ([{"type": "refusal", "refusal": refusal}] if refusal is not None
               else [{"type": "output_text", "text": json.dumps(data), "annotations": []}])
    return {
        "id": "resp_fake", "object": "response", "model": "gpt-6-luna", "status": status,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "output": [{"type": "reasoning", "id": "rs_1", "summary": []},
                   {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                    "content": content}],
        "usage": {"input_tokens": in_tok, "input_tokens_details": {"cached_tokens": cached, "cache_write_tokens": 0},
                  "output_tokens": out_tok, "output_tokens_details": {"reasoning_tokens": reasoning},
                  "total_tokens": in_tok + out_tok},
    }


class FakeResponse:
    def __init__(self, body: dict):
        self.body = body

    def model_dump(self) -> dict:
        return json.loads(json.dumps(self.body))


def fake_answer(params: dict) -> dict:
    """Deterministic answers keyed by the response schema."""
    props = params["text"]["format"]["schema"]["properties"]
    texts = [b["text"] for b in params["input"][0]["content"] if b["type"] == "input_text"]
    aliases = [m for t in texts for m in ALIAS.findall(t)]
    if "focus" in props:  # where the faces are; the first answer is out of range (clamped to 1)
        return {"focus": [{"id": a, "x": 0.3, "y": 1.4 if i == 0 else 0.25} for i, a in enumerate(aliases)]}
    if "photos" in props:  # score
        return {"photos": [{"id": a, "tech": 5 + i % 5, "aesthetic": 4 + (i * 3) % 6, "interest": 3 + (i * 7) % 7,
                            "flags": ["eyes_closed"] if i % 13 == 5 else [], "tags": ["landscape", "city"][i % 2:][:1],
                            "reason": "fake"} for i, a in enumerate(aliases)]}
    if "selected" in props:  # rank
        k = int(re.search(r"Select exactly (\d+)", texts[0]).group(1))
        sel = aliases[::-1][:k]
        return {"selected": sel, "hero": sel[0], "strong": sel[:1], "notes": "fake ranking"}
    if "chapters" in props:  # all chapter titles in one call
        sub = "subtitle" in props["chapters"]["items"]["properties"]
        out = []
        for t in texts:
            m = re.match(r"Chapter (\S+): place (.*?), ", t)
            if m:
                rec = {"id": m.group(1), "title": f"Days in {m.group(2)} and more words here"}
                if sub:
                    rec["subtitle"] = f"A fake subtitle for {m.group(1)}"
                out.append(rec)
        return {"chapters": out}
    if "book_title" in props:
        rec = {"book_title": "Fake Book Title", "cover_photo": aliases[-1]}
        if "subtitle" in props:
            rec["subtitle"] = "fake subtitle"
        return rec
    if "markers" in props:  # AI check of favourite marks: confirm all, plus one invented mark
        stats = json.loads(texts[0].split("Detected marks:\n", 1)[1].split("\nAll folder names")[0])
        return {"markers": [{"kind": s["kind"], "pattern": s["pattern"], "is_favourite": True,
                             "level": 1, "reason": "fake"} for s in stats]
                + [{"kind": "folder", "pattern": "invented", "is_favourite": True, "level": 2, "reason": "x"}]}
    if "questions" in props:
        return {"questions": [
            {"id": "scope", "text": "How long?", "kind": "single",
             "options": [{"value": "highlights", "label": "Short"}, {"value": "complete", "label": "Long"}],
             "default": "complete", "maps_to": "scope", "why": ""},
            {"id": "bogus", "text": "Bad field", "kind": "single", "options": [], "default": "", "maps_to": "nope", "why": ""},
            {"id": "lang", "text": "Language?", "kind": "single",
             "options": [{"value": "de", "label": "Deutsch"}, {"value": "xx", "label": "invalid"}],
             "default": "xx", "maps_to": "language", "why": ""},
        ]}
    raise AssertionError(f"unexpected schema {list(props)}")


class FakeInputTokens:
    def count(self, *, model, instructions, input):
        n = sum(1 for b in input[0]["content"] if b["type"] == "input_image")
        return SimpleNamespace(object="response.input_tokens", input_tokens=12 + 520 * n)


class FakeResponses:
    def __init__(self, answer):
        self.calls: list[dict] = []
        self.answer = answer
        self.respond = lambda params: response_body(self.answer(params))
        self.input_tokens = FakeInputTokens()

    def create(self, **params):
        self.calls.append(params)
        return FakeResponse(self.respond(params))


class FakeFiles:
    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.uploads: list[tuple[str, str]] = []

    def create(self, *, file, purpose):
        name, data = file
        fid = f"file_{len(self.store)}"
        self.store[fid] = data
        self.uploads.append((name, purpose))
        return SimpleNamespace(id=fid)

    def content(self, file_id):
        return SimpleNamespace(text=self.store[file_id].decode("utf-8"))

    def delete(self, file_id):
        self.deleted.append(file_id)
        return SimpleNamespace(id=file_id, deleted=True)


class FakeBatches:
    def __init__(self, responses: FakeResponses, files: FakeFiles):
        self.responses, self.files = responses, files
        self.created: list[dict] = []
        self.state: dict[str, SimpleNamespace] = {}

    def create(self, *, input_file_id, endpoint, completion_window):
        self.created.append({"input_file_id": input_file_id, "endpoint": endpoint, "window": completion_window})
        lines = [json.loads(x) for x in self.files.store[input_file_id].decode("utf-8").splitlines() if x.strip()]
        out = []
        for ln in reversed(lines):  # results come back in any order
            assert ln["method"] == "POST" and ln["url"] == "/v1/responses"
            self.responses.calls.append(ln["body"])
            out.append(json.dumps({"id": "batch_req", "custom_id": ln["custom_id"], "error": None,
                                   "response": {"status_code": 200, "request_id": "r",
                                                "body": self.responses.respond(ln["body"])}}))
        out_id = self.files.create(file=("out.jsonl", "\n".join(out).encode()), purpose="batch_output").id
        bid = f"batch_{len(self.state)}"
        self.state[bid] = SimpleNamespace(id=bid, status="completed", output_file_id=out_id, error_file_id=None,
                                          request_counts=SimpleNamespace(total=len(lines), completed=len(lines),
                                                                         failed=0))
        return SimpleNamespace(id=bid, status="validating")

    def retrieve(self, batch_id):
        return self.state[batch_id]


class FakeOpenAI:
    def __init__(self, answer=fake_answer):
        self.responses = FakeResponses(answer)
        self.files = FakeFiles()
        self.batches = FakeBatches(self.responses, self.files)


@pytest.fixture
def fake_sdk():
    return FakeOpenAI()


@pytest.fixture(scope="session")
def fav_scanned(tmp_path_factory) -> Work:
    """A small trip where the person marked favourites: a Favoriten folder and "+"/"++" file names."""
    from autobook.pipeline.scan import run_scan

    root = tmp_path_factory.mktemp("favtrip") / "2026-07 Portugal"
    photos = generate_trip(root, stops=SMALL_STOPS, long_edge=900, heic_every=0, seed=5)
    by_folder: dict[Path, list[Path]] = {}
    for p in photos:
        by_folder.setdefault(p.path.parent, []).append(p.path)
    for folder, files in by_folder.items():
        fav = folder / "Favoriten"
        fav.mkdir()
        for i, f in enumerate(files):
            if i % 5 == 1:  # every 5th photo moved into Favoriten, some of them marked best with "+"
                f.rename(fav / (("+" if i % 10 == 1 else "") + f.name))
            elif i % 11 == 3:
                f.rename(folder / ("++" + f.name))  # a top favourite outside the folder
    work = Work(tmp_path_factory.mktemp("favwork"))
    run_scan([root], work)
    return work


@pytest.fixture
def fav_work(fav_scanned, tmp_path) -> Work:
    dst = tmp_path / "work"
    shutil.copytree(fav_scanned.root, dst)
    return Work(dst)
