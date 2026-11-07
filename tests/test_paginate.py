"""Which photos share a page and where the full pages go (export/paginate.py)."""

from __future__ import annotations

from autobook.export.fill import page_cost
from autobook.export.paginate import Book, ChapterPages, Page, balance, paginate

BOX = (8.0, 8.0, 281.0, 194.0)  # A4 landscape, narrow frame
LAND, PORT = 4 / 3, 3 / 4


def book(chapters, shapes, **kw) -> Book:
    ids = [i for ch in chapters for pg in ch.pages for i in pg]
    return Book(chapters=chapters, shape={i: (shapes.get(i, LAND), 1.0) for i in ids},
                time={i: float(n) for n, i in enumerate(ids)}, box=BOX, gap=4.0, max_per_page=6,
                title="zz/title", **kw)


def chapter(group, pages, **kw) -> ChapterPages:
    return ChapterPages(group=group, opener=f"zz/div_{group}", pages=pages, **kw)


def photo_pages(res, group=None):
    return [p.ids for p in res.pages if p.kind == "photos" and (group is None or p.chapter == group)]


def test_balancing_keeps_the_order_and_lowers_the_cost():
    # four portraits that can't fill a landscape page evenly next to two landscapes: moving across fits better
    ids = [f"a{i}" for i in range(6)]
    shapes = {i: PORT for i in ids[:4]}
    pages = [Page("photos", ids[:4], "a"), Page("photos", ids[4:], "a")]
    cost = lambda xs: page_cost([(shapes.get(i, LAND), 1.0) for i in xs], BOX, 4.0)  # noqa: E731
    before = sum(cost(p.ids) for p in pages)
    moves = balance(pages, cost, 6)
    assert moves >= 1 and sum(cost(p.ids) for p in pages) < before
    assert [i for p in pages for i in p.ids] == ids  # the order of the photos is unchanged
    assert all(1 <= len(p.ids) <= 6 for p in pages)


def test_balancing_stays_inside_a_chapter_and_leaves_your_own_split():
    pages = [Page("photos", ["a0", "a1", "a2", "a3", "a4"], "a"), Page("photos", ["b0"], "b"),
             Page("photos", ["c0", "c1", "c2", "c3", "c4", "c5"], "c", locked=True), Page("photos", ["c6"], "c")]
    cost = lambda xs: page_cost([(LAND, 1.0)] * len(xs), BOX, 4.0)  # noqa: E731
    balance(pages, cost, 6)
    assert pages[0].ids == ["a0", "a1", "a2", "a3", "a4"] and pages[1].ids == ["b0"]
    assert pages[2].ids == ["c0", "c1", "c2", "c3", "c4", "c5"]


def test_title_page_first_and_every_chapter_page_on_the_left():
    chs = [chapter("a", [["a0", "a1", "a2"], ["a3", "a4"], ["a5", "a6", "a7"]]),
           chapter("b", [["b0", "b1"], ["b2", "b3", "b4"]]),
           chapter("c", [["c0", "c1", "c2", "c3"]])]
    res = paginate(book(chs, {}))
    assert res.pages[0].kind == "title"
    for n, p in enumerate(res.pages, 1):
        if p.kind == "opener":
            assert n % 2 == 0, (n, p)
            assert res.pages[n].kind == "photos"  # facing the chapter's first photos
    assert [i for ids in photo_pages(res) for i in ids] == [i for ch in chs for pg in ch.pages for i in pg]
    assert not any(p.kind == "blank" for p in res.pages)


def test_page_step_by_splitting_or_merging_not_blank_pages():
    chs = [chapter("a", [["a0", "a1", "a2"], ["a3", "a4", "a5"], ["a6", "a7"]])]
    res = paginate(book(chs, {}, page_step=2))
    assert len(res.pages) % 2 == 0 and res.blanks == 0
    res = paginate(book(chs, {}, page_step=2, min_pages=24))
    assert res.blanks > 0 and (len(res.pages) + res.blanks) == 24  # too few photos: blank pages remain


def test_a_photo_with_a_page_to_itself():
    chs = [chapter("a", [["a0", "a1", "a2", "a3", "a4"]])]
    res = paginate(book(chs, {}, own_page=frozenset({"a2"})))
    pages = photo_pages(res)
    assert ["a2"] in pages
    assert [i for ids in pages for i in ids] == ["a0", "a1", "a2", "a3", "a4"]


def test_your_own_split_is_kept():
    chs = [chapter("a", [["a0"], ["a1", "a2", "a3", "a4", "a5"]], custom=True)]
    res = paginate(book(chs, {}, page_step=2))  # title, chapter page and two photo pages: nothing to fix
    assert photo_pages(res, "a") == [["a0"], ["a1", "a2", "a3", "a4", "a5"]]  # balancing would move a1


def test_a_panorama_starts_on_a_left_page_after_the_photos_before_it():
    chs = [chapter("a", [["a0", "a1", "a2"], ["a3", "a4", "a5"], ["a6", "a7"]],
                   spreads=[(2.5, "zz/pano_l", "zz/pano_r")])]
    b = book(chs, {})
    res = paginate(b)
    kinds = [p.kind for p in res.pages]
    k = kinds.index("spread_l")
    assert kinds[k + 1] == "spread_r" and (k + 1) % 2 == 0  # page number k + 1 is even: a left page
    before = [i for p in res.pages[:k] if p.kind == "photos" for i in p.ids]
    assert all(b.time[i] <= 2.5 for i in before)


def test_a_thin_book_spreads_its_photos_before_blank_pages():
    chs = [chapter("a", [["a0", "a1", "a2", "a3"], ["a4", "a5", "a6", "a7"], ["a8", "a9", "a10", "a11"]])]
    res = paginate(book(chs, {}, page_step=2, min_pages=10))
    assert len(res.pages) == 10 and res.blanks == 0  # title, chapter page and 8 photo pages
    assert [i for ids in photo_pages(res) for i in ids] == [f"a{i}" for i in range(12)]
