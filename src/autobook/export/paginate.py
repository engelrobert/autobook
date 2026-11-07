"""Which photos share a page, and where the full pages go (after Fotobuch, before the even fill).

Fotobuch makes a first split of each chapter's photos into pages. autobook then
  * uses your own page split for chapters where you moved photos (Chapter.pages),
  * gives "Ganze Seite" photos a page to themselves,
  * moves a photo to the neighbouring page of its chapter when both pages then fit
    better; the order of the photos stays as it is,
  * starts the book with a title page and puts every chapter page and panorama on a left
    page, facing what follows, by splitting or merging one page before it,
  * reaches the printer's page step by splitting or merging a page instead of adding a blank one.
Page numbers count from 1 (the first page after the cover, a right-hand page); even = left.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autobook.export.fill import Box, page_cost

MIN_GAIN = 0.01  # a move must improve the summed page cost by at least this much
COUNT_WEIGHT = 0.02  # per squared photo away from the book's photos per page: pages stay alike in size


@dataclass
class Page:
    kind: str  # photos | title | opener | spread_l | spread_r | blank
    ids: list[str]  # Fotobuch photo ids
    chapter: str | None = None  # Fotobuch group of the chapter
    locked: bool = False  # your own page split or a page to itself: kept as it is


@dataclass
class ChapterPages:
    group: str
    opener: str | None  # generated chapter page
    pages: list[list[str]]  # photo ids per page, in book order
    custom: bool = False  # your own page split
    spreads: list[tuple[float, str, str]] = field(default_factory=list)  # (time, left page, right page)


@dataclass
class Book:
    chapters: list[ChapterPages]
    shape: dict[str, tuple[float, float]]  # photo id -> (aspect, area weight)
    time: dict[str, float]  # photo id -> capture time (seconds)
    box: Box
    gap: float
    max_per_page: int
    title: str | None = None
    per_page: float = 4.5  # photos per page the book aims for
    own_page: frozenset[str] = frozenset()
    page_step: int = 1
    min_pages: int = 0
    max_pages: int = 0


@dataclass
class Result:
    pages: list[Page]
    blanks: int = 0  # blank pages still needed at the end for the printer's minimum
    moves: int = 0
    splits: int = 0
    merges: int = 0


def paginate(book: Book) -> Result:
    res = Result(pages=[])

    def cost(ids):  # how well the photos fill the page, plus a little for straying from the usual count
        fit = page_cost([book.shape[i] for i in ids], book.box, book.gap)
        return fit + COUNT_WEIGHT * (len(ids) - book.per_page) ** 2

    seq: list[Page] = [Page("title", [book.title])] if book.title else []
    for ch in book.chapters:
        pages = _own_pages(ch, book.own_page)
        res.moves += balance(pages, cost, book.max_per_page)
        if ch.opener:
            seq.append(Page("opener", [ch.opener], ch.group))
        seq += _with_spreads(pages, ch, book.time)
    while book.min_pages and len(seq) < book.min_pages:  # a thin book spreads its photos over more pages
        if not _shift(seq, list(range(len(seq))), book, cost, res, +1, only=True):
            break
    _sides(seq, book, cost, res)
    _page_count(seq, book, cost, res)
    res.pages = seq
    return res


def _own_pages(ch: ChapterPages, own: frozenset[str]) -> list[Page]:
    """The chapter's photo pages; a photo with a page to itself splits its page in three."""
    out = []
    for ids in ch.pages:
        run: list[str] = []
        for i in ids:
            if i in own:
                if run:
                    out.append(Page("photos", run, ch.group, ch.custom))
                out.append(Page("photos", [i], ch.group, True))
                run = []
            else:
                run.append(i)
        if run:
            out.append(Page("photos", run, ch.group, ch.custom))
    return out


def balance(pages: list[Page], cost, max_per_page: int) -> int:
    """Move the last photo of a page to the front of the next one, or the other way round,
    while that lowers the summed cost of both. Returns the number of moves."""
    moves, limit = 0, 10 * len(pages) + 10
    improved = True
    while improved and moves < limit:
        improved = False
        for a, b in zip(pages, pages[1:]):
            if a.locked or b.locked or a.chapter != b.chapter or a.kind != "photos" or b.kind != "photos":
                continue
            now = cost(a.ids) + cost(b.ids)
            for na, nb in ((a.ids[:-1], a.ids[-1:] + b.ids), (a.ids + b.ids[:1], b.ids[1:])):
                if na and nb and len(na) <= max_per_page and len(nb) <= max_per_page \
                        and cost(na) + cost(nb) < now - MIN_GAIN:
                    a.ids, b.ids = na, nb
                    moves += 1
                    improved = True
                    break
    return moves


def _with_spreads(pages: list[Page], ch: ChapterPages, time: dict[str, float]) -> list[Page]:
    """Insert each panorama after the last page whose photos were all taken before it."""
    out = list(pages)
    for ts, left, right in sorted(ch.spreads):
        at = 0
        for k, p in enumerate(out):
            if p.kind == "photos" and p.ids and max(time.get(i, 0) for i in p.ids) <= ts:
                at = k + 1
        out[at:at] = [Page("spread_l", [left], ch.group, True), Page("spread_r", [right], ch.group, True)]
    return out


def _options(seg: list[int], seq: list[Page], cost, max_per_page: int, locked_ok: bool):
    """(cost change, +1 split | -1 merge, index, split point) for the photo pages at positions seg."""
    opts = []
    for k in seg:
        p = seq[k]
        if p.kind != "photos" or (p.locked and not locked_ok):
            continue
        if len(p.ids) >= 2:
            base = cost(p.ids)
            for cut in range(1, len(p.ids)):
                opts.append((cost(p.ids[:cut]) + cost(p.ids[cut:]) - base, +1, k, cut))
        if k + 1 in seg:
            q = seq[k + 1]
            if q.kind == "photos" and q.chapter == p.chapter and (not q.locked or locked_ok) \
                    and len(p.ids) + len(q.ids) <= max_per_page:
                opts.append((cost(p.ids + q.ids) - cost(p.ids) - cost(q.ids), -1, k, 0))
    return opts


def _apply(seq: list[Page], opt) -> None:
    _, change, k, cut = opt
    p = seq[k]
    if change > 0:
        seq[k:k + 1] = [Page("photos", p.ids[:cut], p.chapter, p.locked),
                        Page("photos", p.ids[cut:], p.chapter, p.locked)]
    else:
        q = seq.pop(k + 1)
        p.ids = p.ids + q.ids


def _shift(seq: list[Page], seg: list[int], book: Book, cost, res: Result, prefer: int = 0,
           only: bool = False) -> bool:
    """Change the number of pages in seg by one (split or merge, the cheapest); prefer +1 / -1 / 0 = either.
    only: nothing but the preferred direction."""
    for locked_ok in (False, True):
        opts = _options(seg, seq, cost, book.max_per_page, locked_ok)
        if prefer:
            opts = [o for o in opts if o[1] == prefer] or ([] if only else opts)
        if opts:
            best = min(opts)
            _apply(seq, best)
            if best[1] > 0:
                res.splits += 1
            else:
                res.merges += 1
            return True
    return False


def _sides(seq: list[Page], book: Book, cost, res: Result) -> None:
    """Chapter pages and panorama starts on left (even) pages."""
    start = 0  # first position whose page count may still change
    k = 0
    while k < len(seq):
        p = seq[k]
        if p.kind in ("opener", "spread_l") and (k + 1) % 2 == 1:  # page number k + 1 is odd: a right page
            seg = list(range(start, k))
            near_max = book.max_pages and len(seq) + 1 > book.max_pages
            if not _shift(seq, seg, book, cost, res, -1 if near_max else 0):
                if p.kind == "spread_l" and k + 2 < len(seq) and seq[k + 2].kind == "photos" \
                        and seq[k + 2].chapter == p.chapter:
                    seq[k:k + 3] = [seq[k + 2], seq[k], seq[k + 1]]  # the panorama one page later
                else:
                    seq.insert(k, Page("blank", [], p.chapter))
            k = 0  # page numbers before this point changed; walk again from the start
            start = 0
            continue
        if p.kind in ("title", "opener", "spread_r"):
            start = k + 1
        k += 1


def _page_count(seq: list[Page], book: Book, cost, res: Result) -> None:
    """The printer's minimum and page step, after the last chapter page or panorama."""
    last = max((k for k, p in enumerate(seq) if p.kind in ("title", "opener", "spread_r")), default=-1)
    while book.min_pages and len(seq) < book.min_pages:  # the page sides may have merged a page
        if not _shift(seq, list(range(last + 1, len(seq))), book, cost, res, +1, only=True):
            break
    if book.min_pages and len(seq) < book.min_pages:  # every page holds one photo: blank pages remain
        res.blanks = book.min_pages - len(seq)
    step = book.page_step or 1
    total = len(seq) + res.blanks
    if step > 1 and total % step:
        # at step 2 one page more or one less both work; at larger steps only splits (pages more)
        prefer = +1 if step > 2 else (-1 if book.max_pages and total + 1 > book.max_pages else 0)
        while total % step and _shift(seq, list(range(last + 1, len(seq))), book, cost, res, prefer):
            total = len(seq) + res.blanks
        if total % step:
            res.blanks += step - total % step
