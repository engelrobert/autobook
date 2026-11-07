"""Thin wrapper over the OpenAI Responses API: structured JSON, batches, cost ledger, budget cap.

Every call uses structured outputs (`text.format` with a strict JSON schema),
sets a reasoning effort and `store=False`. Task modules build provider-neutral
AIRequests (text blocks + `image_block`); only this module knows the API shape.
"""

from __future__ import annotations

import base64
import json
import math
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from importlib import resources

from autobook.catalog import Catalog

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_EFFORT = "high"  # max was ~5 min for 32 photos in the first live test (2026-10-30)
DEFAULT_MAX_OUTPUT = 64_000  # gpt-6-luna allows 128k; max-effort reasoning needs the room

# USD per million tokens (developers.openai.com/api/docs/pricing, 2026-10-30, prompts < 272k tokens).
PRICES = {
    "gpt-6-luna": {"in": 0.10, "cached": 0.01, "out": 0.50},
    "gpt-6.1-terra": {"in": 1.00, "cached": 0.05, "out": 5.00},
    "gpt-6.1-sol": {"in": 2.00, "cached": 0.10, "out": 10.00},
    "gpt-6-astra": {"in": 10.00, "cached": 1.00, "out": 50.00},
}
BATCH_FACTOR = 0.5
CACHE_WRITE_FACTOR = 1.25  # GPT-5.6 and later: cache writes cost 1.25x input (30-min cache lifetime)
# Budget estimate of reasoning tokens (the ledger records real usage). Measured with gpt-6-luna at
# effort max on 2026-10-30: scoring ~300 per photo, ranking ~22k per chapter, titles ~200 per call;
# at high, roughly 1/10 of that. Kept a little above the measurements on purpose.
REASONING_AT_MAX = {"score": 350, "rank": 25_000}  # score: per image; others: per call
REASONING_AT_MAX_DEFAULT = 1_000
EFFORT_FACTOR = {"none": 0.0, "low": 0.03, "medium": 0.07, "high": 0.15, "xhigh": 0.4, "max": 1.0}
IMAGE_PATCH_MULTIPLIER = 1.2  # tokens per 32-px patch (documented for gpt-6-astra / gpt-5.6; check with ai-calibrate)
AUTO_BATCH_USD = 2.0  # batch=None: use the Batch API (can take hours) only for runs estimated above this
MAX_BATCH_BYTES = 150_000_000  # OpenAI's limit is 200 MB per input file
BATCH_DONE = {"completed", "failed", "expired", "cancelled"}


# Measured per-task costs at effort "high" (2026-10-30, gpt-6-luna, Jura 1406 photos): scoring
# ~$0.11 per 1000 photos, editing ~$0.012 per chapter (more with favourites/mix), titles ~$0.005.
# Other efforts scale roughly like this (max took ~4x the reasoning of high per photo).
_EFFORT_COST = {"none": 0.3, "low": 0.5, "medium": 0.7, "high": 1.0, "xhigh": 2.0, "max": 3.9}


def estimate_book(n_photos: int, n_chapters: int, effort: str = "high", parallel: int = 8,
                  score: bool = True) -> dict:
    """Rough cost (USD) and duration (minutes) of an AI selection, for the browser's confirmation."""
    f = _EFFORT_COST.get(effort, 1.0)
    usd = ((0.00011 * n_photos if score else 0.0) + 0.02 * n_chapters + 0.01) * f
    calls = math.ceil(n_photos * 0.85 / 30) if score else 0
    minutes = (math.ceil(calls / parallel) * 0.6 + math.ceil(n_chapters * 1.5 / parallel) * 1.5 + 0.3) * f
    return {"usd": round(usd, 2), "minutes": max(1, round(minutes))}


class BudgetExceeded(RuntimeError):
    pass


class AIError(RuntimeError):
    pass


@dataclass
class AIRequest:
    custom_id: str
    system: str
    content: list[dict]  # {"type": "text", "text": ...} and image_block(...) items
    schema: dict
    kind: str
    max_tokens: int = DEFAULT_MAX_OUTPUT
    image_tokens: int = 0  # estimate, for the budget check
    model: str | None = None  # None = the client's model
    effort: str | None = None  # None = the client's effort


@dataclass
class AIResult:
    custom_id: str
    data: dict | None = None
    error: str | None = None
    usd: float = 0.0
    usage: dict = field(default_factory=dict)


def load_prompt(name: str) -> tuple[str, str]:
    """Return (version, text) of a prompt file; the first line is 'version: N'."""
    text = resources.files("autobook.ai.prompts").joinpath(f"{name}.md").read_text(encoding="utf-8")
    first, _, body = text.partition("\n")
    version = first.split(":", 1)[1].strip() if first.lower().startswith("version:") else "0"
    return f"{name}@{version}", body.strip()


def image_block(jpeg: bytes, detail: str = "high") -> dict:
    """Responses API image item. Thumbnails are already small, so 'high' keeps them as sent."""
    return {"type": "input_image", "detail": detail,
            "image_url": "data:image/jpeg;base64," + base64.standard_b64encode(jpeg).decode("ascii")}


def _to_openai(block: dict) -> dict:
    if block["type"] == "text":
        return {"type": "input_text", "text": block["text"]}
    return block


def estimate_image_tokens(width: int, height: int) -> int:
    """32-px patches × multiplier (OpenAI's patch method); check real numbers with `autobook ai-calibrate`."""
    return math.ceil(math.ceil(width / 32) * math.ceil(height / 32) * IMAGE_PATCH_MULTIPLIER)


def _rates(model: str) -> dict:
    # unknown model: estimate with the most expensive known rates rather than too low
    return PRICES.get(model) or max(PRICES.values(), key=lambda r: r["out"])


def price(model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0, batch: bool = False,
          cache_write_tokens: int = 0) -> float:
    """input_tokens includes cached and cache-write tokens (as OpenAI reports them); each input token is
    billed at exactly one rate. Output includes reasoning tokens."""
    r = _rates(model)
    cached = min(cached_tokens, input_tokens)
    written = min(cache_write_tokens, input_tokens - cached)
    plain = input_tokens - cached - written
    usd = (plain * r["in"] + cached * r["cached"] + written * r["in"] * CACHE_WRITE_FACTOR
           + output_tokens * r["out"]) / 1e6
    return usd * (BATCH_FACTOR if batch else 1.0)


def parse_response(body: dict) -> dict:
    """Responses API object (as a dict) -> the JSON our schema asked for."""
    status = body.get("status")
    if status == "incomplete":
        reason = (body.get("incomplete_details") or {}).get("reason")
        raise AIError(f"response cut off ({reason})")
    if status in ("failed", "cancelled"):
        raise AIError(f"response {status}: {(body.get('error') or {}).get('message', '')}")
    texts = []
    for item in body.get("output") or []:
        if item.get("type") != "message":
            continue
        for c in item.get("content") or []:
            if c.get("type") == "refusal":
                raise AIError(f"model declined: {c.get('refusal', '')[:200]}")
            if c.get("type") == "output_text":
                texts.append(c.get("text") or "")
    if not texts:
        raise AIError("no text in response")
    return json.loads("".join(texts))


def usage_of(body: dict) -> dict:
    u = body.get("usage") or {}
    details = u.get("input_tokens_details") or {}
    return {"input_tokens": u.get("input_tokens") or 0,
            "cached_tokens": details.get("cached_tokens") or 0,
            "cache_write_tokens": details.get("cache_write_tokens") or 0,
            "output_tokens": u.get("output_tokens") or 0,
            "reasoning_tokens": (u.get("output_tokens_details") or {}).get("reasoning_tokens") or 0}


class AIClient:
    """Runs AIRequests; records every call in the catalog's cost ledger."""

    def __init__(self, catalog: Catalog, *, api_key: str | None = None, max_cost: float = 5.0,
                 sdk_client=None, concurrency: int = 8, model: str = DEFAULT_MODEL,
                 effort: str = DEFAULT_EFFORT, timeout: float = 1800.0):
        if sdk_client is None:
            import openai

            # max-effort reasoning can take minutes per call
            sdk_client = openai.OpenAI(api_key=api_key, max_retries=4, timeout=timeout)
        self.sdk = sdk_client
        self.catalog = catalog
        self.max_cost = max_cost
        self.concurrency = concurrency
        self.model = model
        self.effort = effort
        self.calls: dict[str, int] = {}  # per task kind, for the error summary
        self.errors: list[dict] = []  # failed requests: {kind, id, error}

    def _model(self, req: AIRequest) -> str:
        return req.model or self.model

    def _effort(self, req: AIRequest) -> str:
        return req.effort or self.effort

    # budget ---------------------------------------------------------------
    def estimate(self, req: AIRequest, batch: bool = False) -> float:
        text_chars = len(req.system) + sum(len(b.get("text", "")) for b in req.content if b["type"] == "text")
        in_tok = text_chars // 3 + req.image_tokens
        n_images = sum(1 for b in req.content if b["type"] == "input_image")
        visible = 60 * n_images + 300
        factor = EFFORT_FACTOR.get(self._effort(req), 1.0)
        reasoning = (REASONING_AT_MAX["score"] * n_images if req.kind == "score"
                     else REASONING_AT_MAX.get(req.kind, REASONING_AT_MAX_DEFAULT))
        out_tok = min(req.max_tokens, visible + round(reasoning * factor))
        return price(self._model(req), in_tok, out_tok, batch=batch)

    def check_budget(self, reqs: list[AIRequest], batch: bool) -> float:
        est = sum(self.estimate(r, batch) for r in reqs)
        spent = self.catalog.total_cost()
        if spent + est > self.max_cost:
            raise BudgetExceeded(
                f"estimated ${est:.2f} on top of ${spent:.2f} already spent exceeds the cap of "
                f"${self.max_cost:.2f} (raise it with --max-cost, or lower --effort)")
        return est

    # requests -------------------------------------------------------------
    def _params(self, req: AIRequest) -> dict:
        return {
            "model": self._model(req),
            "instructions": req.system,
            "input": [{"role": "user", "content": [_to_openai(b) for b in req.content]}],
            "max_output_tokens": req.max_tokens,
            "reasoning": {"effort": self._effort(req)},
            "text": {"format": {"type": "json_schema", "name": req.kind, "schema": req.schema, "strict": True}},
            "store": False,  # do not keep responses (and the thumbnails in them) on OpenAI's side
        }

    def _record(self, req: AIRequest, body: dict, batch: bool, seconds: float | None) -> tuple[float, dict]:
        u = usage_of(body)
        model = self._model(req)
        usd = price(model, u["input_tokens"], u["output_tokens"], u["cached_tokens"], batch,
                    cache_write_tokens=u["cache_write_tokens"])
        self.catalog.add_cost(model, req.kind, u["input_tokens"], u["output_tokens"], u["cached_tokens"],
                              usd, batch, reasoning=u["reasoning_tokens"], seconds=seconds)
        return usd, u

    def _result(self, req: AIRequest, body: dict, batch: bool, seconds: float | None = None) -> AIResult:
        usd, usage = self._record(req, body, batch, seconds)
        try:
            return AIResult(req.custom_id, data=parse_response(body), usd=usd, usage=usage)
        except (AIError, json.JSONDecodeError) as e:
            return AIResult(req.custom_id, error=str(e), usd=usd, usage=usage)

    def _call_one(self, req: AIRequest) -> AIResult:
        start = time.monotonic()
        try:
            resp = self.sdk.responses.create(**self._params(req))
        except Exception as e:
            return AIResult(req.custom_id, error=f"{type(e).__name__}: {e}")
        return self._result(req, resp.model_dump(), batch=False, seconds=time.monotonic() - start)

    def run(self, reqs: list[AIRequest], *, batch: bool | None = None,
            progress: Callable[[str, int, int], None] | None = None,
            poll_seconds: float = 20.0) -> dict[str, AIResult]:
        """Run requests directly (parallel) or through the Batch API (50% cheaper, up to 24 h).

        batch=None decides automatically: batch only when the estimated cost exceeds AUTO_BATCH_USD,
        because a batch can take hours and saves little on a normal book.
        """
        if not reqs:
            return {}
        out = self._run(reqs, batch, progress, poll_seconds)
        by_id = {r.custom_id: r for r in reqs}
        for cid, res in out.items():
            kind = by_id[cid].kind
            self.calls[kind] = self.calls.get(kind, 0) + 1
            if res.error:
                self.errors.append({"kind": kind, "id": cid, "error": res.error})
        return out

    FALLBACK = {"score": "those photos use the offline score", "rank": "those chapters use the offline selection",
                "title": "those chapters keep place-name titles", "book": "the book title comes from place names",
                "questions": "the default questions are used"}

    def error_summary(self) -> list[str]:
        """One line per task kind with failures: how many, what went wrong first, what autobook did instead."""
        lines = []
        for kind in dict.fromkeys(e["kind"] for e in self.errors):
            errs = [e for e in self.errors if e["kind"] == kind]
            lines.append(f"AI {kind}: {len(errs)} of {self.calls.get(kind, len(errs))} calls failed "
                         f"({errs[0]['error'][:160]}); {self.FALLBACK.get(kind, 'autobook used its offline choice')}")
        return lines

    def _run(self, reqs: list[AIRequest], batch: bool | None, progress, poll_seconds: float) -> dict[str, AIResult]:
        est_direct = sum(self.estimate(r) for r in reqs)
        if batch is None:
            batch = len(reqs) > 1 and est_direct > AUTO_BATCH_USD
        self.check_budget(reqs, batch)
        progress = progress or (lambda *_: None)
        if not batch:
            out: dict[str, AIResult] = {}
            with ThreadPoolExecutor(self.concurrency) as ex:
                for i, res in enumerate(ex.map(self._call_one, reqs), 1):
                    out[res.custom_id] = res
                    progress("ai", i, len(reqs))
            return out
        out = {}
        for chunk in self._batch_chunks(reqs):
            out.update(self._run_batch(chunk, progress, poll_seconds))
        return out

    # batch ------------------------------------------------------------------
    def _jsonl_line(self, req: AIRequest) -> bytes:
        line = {"custom_id": req.custom_id, "method": "POST", "url": "/v1/responses", "body": self._params(req)}
        return (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")

    def _batch_chunks(self, reqs: list[AIRequest]) -> list[list[AIRequest]]:
        """Split so each JSONL file stays well under OpenAI's 200 MB limit."""
        chunks, cur, size = [], [], 0
        for r in reqs:
            n = len(self._jsonl_line(r))
            if cur and size + n > MAX_BATCH_BYTES:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(r)
            size += n
        if cur:
            chunks.append(cur)
        return chunks

    def _run_batch(self, reqs: list[AIRequest], progress, poll_seconds: float) -> dict[str, AIResult]:
        by_id = {r.custom_id: r for r in reqs}
        data = b"".join(self._jsonl_line(r) for r in reqs)
        uploaded = self.sdk.files.create(file=("autobook-batch.jsonl", data), purpose="batch")
        file_ids = [uploaded.id]
        out: dict[str, AIResult] = {}
        try:
            batch = self.sdk.batches.create(input_file_id=uploaded.id, endpoint="/v1/responses",
                                            completion_window="24h")
            self.catalog.kv_set("last_batch_id", batch.id)
            self.catalog.commit()
            while True:
                batch = self.sdk.batches.retrieve(batch.id)
                counts = getattr(batch, "request_counts", None)
                done = ((getattr(counts, "completed", 0) or 0) + (getattr(counts, "failed", 0) or 0)) if counts else 0
                progress("ai-batch", done, len(reqs))
                if batch.status in BATCH_DONE:
                    break
                time.sleep(poll_seconds)
            for fid in (getattr(batch, "output_file_id", None), getattr(batch, "error_file_id", None)):
                if not fid:
                    continue
                file_ids.append(fid)
                for raw in self.sdk.files.content(fid).text.splitlines():
                    if not raw.strip():
                        continue
                    line = json.loads(raw)
                    req = by_id.get(line.get("custom_id"))
                    if req is None:
                        continue
                    resp = line.get("response") or {}
                    if line.get("error") or resp.get("status_code", 200) != 200:
                        err = line.get("error") or (resp.get("body") or {}).get("error") or {}
                        out[req.custom_id] = AIResult(req.custom_id, error=f"batch item failed: {err}")
                        continue
                    out[req.custom_id] = self._result(req, resp.get("body") or {}, batch=True)
        finally:
            for fid in file_ids:  # thumbnails must not stay stored at OpenAI
                try:
                    self.sdk.files.delete(fid)
                except Exception:
                    pass
        for cid in by_id:
            out.setdefault(cid, AIResult(cid, error=f"missing from batch results (batch {batch.status})"))
        return out

    def count_tokens(self, req: AIRequest) -> int:
        p = self._params(req)
        r = self.sdk.responses.input_tokens.count(model=p["model"], instructions=p["instructions"], input=p["input"])
        return r.input_tokens
