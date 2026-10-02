"""True isolated Job and four-format render acceptance; no live model/data."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import zipfile
from datetime import datetime


def support():
    path = Path(os.environ["VALIDATION_FIXTURE_ROOT"]) / "gate_support.py"
    spec = importlib.util.spec_from_file_location("top10_final_support", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_own_job_deadline_contains_grandchild(tmp_path, monkeypatch):
    support().exercise_job("top10", tmp_path / "job", monkeypatch)


def test_real_four_formats_same_rank_and_receipt(tmp_path, monkeypatch):
    fixture = support().load(Path(__file__).with_name("test_reporting_performance_contract.py"), "gate_top10_report_fixtures")
    import reporting
    fixture.clock(monkeypatch, "2026-09-23T07:31:00+08:00")
    frame = fixture.rows()
    coverage = frame[["market", "stock_id", "name"]].assign(status="analyzed")
    meta = {"asof": "2026-09-22", "next_session": "2026-09-23",
        "order21_support_by_identity": {"TWSE:2330": {"order21_confidence_score": 99},
                                       "TWSE:2317": {"order21_confidence_score": 1}}}
    monkeypatch.setenv("TOP10_EXCEL_VERIFY", "1")
    checks = tmp_path / "excel-checks"
    checks.mkdir()
    monkeypatch.setenv("TOP10_EXCEL_CHECK_DIR", str(checks))
    path = reporting.report(Path(__file__).resolve().parents[1], frame, coverage, meta, {"model_version": "fixture"},
        datetime.fromisoformat("2026-09-23T07:00:00+08:00"), namespace_root=tmp_path / "namespace",
        report_context=fixture.context(), performance_state={"status": "pending"})
    payload = json.loads(path.with_suffix(".json").read_text(encoding="utf-8"))
    assert [row["stock_id"] for row in payload["top10"]] == ["2317", "2330"]
    assert payload["top10"][0]["probability"] == .601235
    html = path.with_suffix(".html").read_text(encoding="utf-8")
    assert html.index("2317") < html.index("2330")
    with path.with_suffix(".csv").open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames[-1] == "order21_support_text"
        assert [row["stock_id"] for row in reader if row["record_type"] == "top10"] == ["2317", "2330"]
    with zipfile.ZipFile(path) as archive:
        content = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist()
            if name.endswith(".xml") and ("worksheets/" in name or "sharedStrings" in name))
    assert "2317" in content and "2330" in content
    assert (checks / "excel_inspection.json").is_file()
    assert list(checks.glob("excel_preview_*.png"))
    # Registration and latest pointer are real writes exclusively in tmp namespace.
    assert (tmp_path / "namespace/reports/latest.json").is_file()
    assert list((tmp_path / "namespace/logs/performance").rglob("*"))
