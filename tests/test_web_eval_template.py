import json
from pathlib import Path

from autobook.brief.models import Brief
from autobook.catalog import Catalog
from autobook.eval.metrics import metrics, run_eval
from autobook.pipeline.select import build_plan


def test_eval_report(work_copy):
    plan = build_plan(work_copy, Brief(target_photos=20))
    favs = [p.id for p in plan.all_photos()][:5] + [r.id for r in plan.rejected][:5]
    (work_copy.root / "gold.json").write_text(json.dumps({"favourites": favs}))
    report = run_eval(work_copy, work_copy.root / "gold.json", ["random", "local", "ai"], brief=Brief(target_photos=20))
    assert "| local |" in report and "| random |" in report and "no API key" in report
    m = metrics({p.id for p in plan.all_photos()}, set(favs), Catalog(work_copy.catalog))
    assert m["recall"] >= 0.5 and m["dup_leak"] == 0


def test_template_is_generated_from_upstream():
    """resources/autobook.typ must equal tools/make_template.py applied to the pinned upstream template."""
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("make_template", root / "tools" / "make_template.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    res = root / "src" / "autobook" / "resources"
    built = mod.build(res / "fotobuch-upstream-v0.3.0.typ")
    assert built == (res / "autobook.typ").read_text(encoding="utf-8")
    assert built.count("#let is_final = false") == 1  # Fotobuch's release build replaces this line
    assert "{project_name}" in built
