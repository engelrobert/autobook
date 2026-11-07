import json

import pytest

from autobook.ai.client import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    AIClient,
    AIRequest,
    BudgetExceeded,
    estimate_image_tokens,
    parse_response,
    price,
)
from autobook.ai.rank import validate
from autobook.brief.models import Brief
from autobook.brief.questions import ai_questions
from autobook.catalog import Catalog
from autobook.pipeline.select import build_plan
from tests.conftest import FakeOpenAI, fake_answer, response_body


def _client(work, sdk, **kw):
    return AIClient(Catalog(work.catalog), sdk_client=sdk, **kw)


def test_ai_plan_uses_one_model_at_default_effort(work_copy, fake_sdk):
    ai = _client(work_copy, fake_sdk)
    plan = build_plan(work_copy, Brief(target_photos=24, language="de"), ai=ai, batch=False)
    assert plan.selection == "ai"
    assert plan.book.title == "Fake Book Title"
    assert all(c.title.startswith("Days in") and len(c.title.split()) <= 4 for c in plan.chapters)  # trimmed
    assert all(c.subtitle == "" for c in plan.chapters) and plan.book.subtitle == ""  # off by default
    titles_calls = [c for c in fake_sdk.responses.calls if c["text"]["format"]["name"] == "title"]
    assert len(titles_calls) == 1  # all chapters in one call: they can't repeat each other
    calls = fake_sdk.responses.calls
    kinds = {c["text"]["format"]["name"] for c in calls}
    assert {"score", "rank", "title", "book"} <= kinds
    for c in calls:
        assert c["model"] == DEFAULT_MODEL == "gpt-6-luna"
        assert c["reasoning"] == {"effort": DEFAULT_EFFORT} and DEFAULT_EFFORT == "high"
        fmt = c["text"]["format"]
        assert fmt["type"] == "json_schema" and fmt["strict"] is True
        assert c["store"] is False and "tool_choice" not in c
        assert c["max_output_tokens"] >= 32_000  # room for max-effort reasoning
        items = c["input"][0]["content"]
        assert {i["type"] for i in items} <= {"input_text", "input_image"}
        assert all(i["image_url"].startswith("data:image/jpeg;base64,") for i in items if i["type"] == "input_image")
    assert any(p.scores.tech is not None for p in plan.all_photos())
    assert ai.catalog.total_cost() > 0


def test_reasoning_tokens_are_recorded(work_copy, fake_sdk):
    build_plan(work_copy, Brief(target_photos=24), ai=_client(work_copy, fake_sdk), batch=False)
    rows = Catalog(work_copy.catalog).cost_summary()
    assert rows and all(r["reasoning_tokens"] > 0 and r["model"] == "gpt-6-luna" for r in rows)


def test_ai_results_are_cached_per_model_and_effort(work_copy, fake_sdk):
    build_plan(work_copy, Brief(target_photos=24), ai=_client(work_copy, fake_sdk), batch=False)
    assert fake_sdk.responses.calls
    again = FakeOpenAI()
    build_plan(work_copy, Brief(target_photos=24), ai=_client(work_copy, again), batch=False)
    assert again.responses.calls == []
    lower = FakeOpenAI()
    build_plan(work_copy, Brief(target_photos=24), ai=_client(work_copy, lower, effort="low"), batch=False)
    assert lower.responses.calls and all(c["reasoning"]["effort"] == "low" for c in lower.responses.calls)


def test_batch_path_uploads_jsonl_and_deletes_files(work_copy, fake_sdk):
    plan = build_plan(work_copy, Brief(target_photos=24), ai=_client(work_copy, fake_sdk), batch=True)
    assert plan.selection == "ai"
    b = fake_sdk.batches
    assert b.created and all(c["endpoint"] == "/v1/responses" and c["window"] == "24h" for c in b.created)
    assert ("autobook-batch.jsonl", "batch") in fake_sdk.files.uploads
    assert set(fake_sdk.files.deleted) == set(fake_sdk.files.store)  # inputs and outputs removed at OpenAI
    ledger = Catalog(work_copy.catalog).db.execute("SELECT DISTINCT batch FROM cost_ledger").fetchall()
    assert (1,) in [tuple(r) for r in ledger]


def test_batch_is_split_by_size(work_copy, fake_sdk, monkeypatch):
    import autobook.ai.client as client

    monkeypatch.setattr(client, "MAX_BATCH_BYTES", 2_000)
    ai = _client(work_copy, fake_sdk)
    reqs = [AIRequest(f"r{i}", "s", [{"type": "text", "text": "x" * 1500}],
                      {"type": "object", "properties": {"photos": {"type": "array"}}}, "score") for i in range(3)]
    fake_sdk.responses.respond = lambda params: response_body({"photos": []})
    res = ai.run(reqs, batch=True, poll_seconds=0)
    assert len(fake_sdk.batches.created) == 3 and all(r.data == {"photos": []} for r in res.values())


def test_budget_cap_stops_before_spending(work_copy, fake_sdk):
    with pytest.raises(BudgetExceeded):
        build_plan(work_copy, Brief(target_photos=24), ai=_client(work_copy, fake_sdk, max_cost=0.0001))
    assert fake_sdk.responses.calls == []


@pytest.mark.parametrize("body,msg", [
    (response_body(None, refusal="I can't help with that."), "declined"),
    (response_body({"x": 1}, status="incomplete"), "max_output_tokens"),
    (dict(response_body({"x": 1}), output=[]), "no text"),
])
def test_refusal_and_incomplete_become_errors(work_copy, body, msg):
    sdk = FakeOpenAI()
    sdk.responses.respond = lambda params: body
    res = _client(work_copy, sdk).run([AIRequest("x", "s", [{"type": "text", "text": "t"}], {}, "score")])
    assert res["x"].error and msg in res["x"].error
    assert res["x"].usd > 0  # still billed, still recorded


def test_parse_matches_the_real_sdk_shape():
    """Our dict parser must read what openai's own Response model dumps."""
    from openai.types.responses import Response

    resp = Response.model_validate({**response_body({"ok": True}), "created_at": 0, "parallel_tool_calls": False,
                                    "tool_choice": "auto", "tools": []})
    assert resp.output_text == json.dumps({"ok": True})
    assert parse_response(resp.model_dump()) == {"ok": True}


def test_failed_ai_chapters_fall_back_to_local(work_copy):
    def answer(params):
        if params["text"]["format"]["name"] in ("rank", "title", "book"):
            raise RuntimeError("boom")
        return fake_answer(params)
    sdk = FakeOpenAI(answer)
    plan = build_plan(work_copy, Brief(target_photos=24), ai=_client(work_copy, sdk), batch=False)
    assert plan.photo_count() > 0 and plan.book.title  # local picks and place-name titles


def test_rank_validation_fills_and_trims():
    cands = [{"id": f"id{i}", "final": 1 - i / 10} for i in range(6)]
    aliases = {f"p{i + 1:02d}": f"id{i}" for i in range(6)}
    out = validate({"selected": ["p02", "p02", "[id=p05]", "zz"], "hero": "p99", "strong": ["p05", "p01"]}, aliases, cands, 4)
    assert out["selected"][:2] == ["id1", "id4"] and len(out["selected"]) == 4
    assert out["hero"] in out["selected"] and set(out["strong"]) <= set(out["selected"])
    out2 = validate({"selected": [f"p0{i}" for i in range(1, 7)], "hero": "p01", "strong": []}, aliases, cands, 3)
    assert out2["selected"] == ["id0", "id1", "id2"]


def test_ai_questions_fall_back_and_filter(work_copy, fake_sdk):
    summary = json.loads(work_copy.summary.read_text())
    qs = ai_questions(_client(work_copy, fake_sdk), work_copy, summary)
    fields = [q.maps_to for q in qs]
    assert "scope" in fields and "nope" not in fields
    lang = next(q for q in qs if q.maps_to == "language")
    assert lang.default == "de" and [o.value for o in lang.options] == ["de"]


def test_count_tokens_uses_responses_endpoint(work_copy, fake_sdk):
    from autobook.ai.client import image_block

    ai = _client(work_copy, fake_sdk)
    n = ai.count_tokens(AIRequest("c", "x", [image_block(b"\xff\xd8jpeg"), {"type": "text", "text": "x"}], {}, "c"))
    assert n == 12 + 520


def test_prices_and_estimates():
    assert price("gpt-6-luna", 1_000_000, 0) == pytest.approx(0.10)
    assert price("gpt-6-luna", 1_000_000, 0, cached_tokens=500_000) == pytest.approx(0.055)  # cached at $0.01
    assert price("gpt-6-luna", 1_000_000, 0, cache_write_tokens=1_000_000) == pytest.approx(0.125)  # 1.25x
    assert price("gpt-6-luna", 0, 1_000_000, batch=True) == pytest.approx(0.25)
    assert price("unknown-model", 1_000_000, 0) == pytest.approx(10.0)  # priced like the dearest known model
    assert estimate_image_tokens(768, 576) == 519  # 24 × 18 patches × 1.2


def test_focus_points_are_asked_once_and_kept_inside_the_photo(work_copy, fake_sdk):
    from autobook.ai.focus import book_photo_ids, find_focus, known_focus

    ai = _client(work_copy, fake_sdk)
    plan = build_plan(work_copy, Brief(target_photos=24), ai=ai, batch=False)
    assert any(c["text"]["format"]["name"] == "focus" for c in fake_sdk.responses.calls)
    ids = book_photo_ids(plan)
    known = known_focus(ai.catalog)
    assert set(ids) <= set(known)
    assert all(0 <= x <= 1 and 0 <= y <= 1 for x, y in known.values())
    assert any(y == 1.0 for _, y in known.values())  # the fake's 1.4 is clamped into the photo
    calls = len(fake_sdk.responses.calls)
    assert set(find_focus(ai, work_copy, ids, batch=False)) == set(ids)
    assert len(fake_sdk.responses.calls) == calls  # cached in the catalog: no second request
