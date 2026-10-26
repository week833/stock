"""Offline fixtures only; never read the installed Central or publish a report."""
import copy
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stock_report_comparison as comparison


def central(tmp_path, routes):
    root = tmp_path / "central"
    (root / "_objects").mkdir(parents=True)
    (root / "_manifests").mkdir()
    (root / "_metadata").mkdir()
    entries = []
    for route, rows in routes.items():
        raw = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        path = "_objects/" + digest + ".json"
        (root / path).write_bytes(raw)
        entries.append(dict(route_id=route, path=path, sha256=digest, size=len(raw), status="PARTIAL"))
    raw = json.dumps({"datasets": entries}, ensure_ascii=False).encode("utf-8")
    head = hashlib.sha256(raw).hexdigest()
    (root / "_manifests" / (head + ".json")).write_bytes(raw)
    (root / "_metadata" / "stock_data_current.json").write_bytes(raw)
    return root, head


def revenue(year, month, value, **fields):
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return dict(stock_id="2330", revenue_year=year, revenue_month=month, revenue=value,
                date=f"{next_year:04}{next_month:02}01", **fields)


def metric(block, key):
    return next(item for item in block["metrics"] if item["id"] == key)


def test_revenue_calculates_history_and_actual_comparable_years(tmp_path):
    rows = [revenue(y, m, 100 if y == 2025 else 120, revenue_unit="TWD")
            for y in (2025, 2026) for m in range(1, 10)]
    rows += [revenue(2023, 9, 80, revenue_unit="TWD"), revenue(2024, 9, 90, revenue_unit="TWD")]
    root, head = central(tmp_path, {"finmind_month_revenue": rows})
    block = comparison.build_comparisons(["2330"], "20261016", central_root=root, head_sha=head)["2330"]
    monthly = metric(block, "revenue_monthly")
    assert monthly["current_value"] == 120
    assert monthly["year_ago_value"] == 100
    assert monthly["yoy"] == pytest.approx(.2)
    assert monthly["mom"] == 0
    assert metric(block, "revenue_ytd")["current_value"] == 1080
    assert metric(block, "revenue_3m")["yoy"] == pytest.approx(.2)
    assert metric(block, "revenue_same_month")["historical_years"] == [2023, 2024, 2025, 2026]
    assert metric(block, "revenue_same_month")["rank"] == 1
    assert monthly["資料期間"] == "202609"
    assert "20261001" in monthly["資料日期"]
    assert block["source_head_sha256"] == head
    assert block["signal_date"] == "2026-10-16"
    assert "較去年同月增加 20.00%" in monthly["解讀"]
    assert "較上月持平 0.00%" in monthly["解讀"]


def test_missing_months_do_not_become_complete_rolling_or_ytd_totals(tmp_path):
    root, head = central(tmp_path, {"finmind_month_revenue": [revenue(2026, 7, 70), revenue(2026, 9, 90)]})
    block = comparison.build_comparisons(["2330"], "20261016", central_root=root, head_sha=head)["2330"]
    item = metric(block, "revenue_3m")
    assert item["current_value"] is None
    assert item["known_sum"] == 160
    assert item["missing_months"] == ["202608"]
    assert "已知" in item["本期值"]
    assert metric(block, "revenue_monthly")["current_value"] == 90


@pytest.mark.parametrize("previous,current,want", [(0, 10, None), (-10, 10, None), (100, 0, -1.0)])
def test_zero_negative_and_zero_current_are_distinguished(tmp_path, previous, current, want):
    root, head = central(tmp_path, {"finmind_month_revenue": [revenue(2025, 9, previous), revenue(2026, 9, current)]})
    item = metric(comparison.build_comparisons(["2330"], "20261016", central_root=root, head_sha=head)["2330"], "revenue_monthly")
    assert item["yoy"] == want
    assert item["delta_yoy"] == current - previous
    assert item["current_value"] == current


def test_unknown_unit_keeps_raw_value_but_allows_same_source_growth(tmp_path):
    root, head = central(tmp_path, {"finmind_month_revenue": [revenue(2025, 9, 100000000), revenue(2026, 9, 120000000)]})
    item = metric(comparison.build_comparisons(["2330"], "20261016", central_root=root, head_sha=head)["2330"], "revenue_monthly")
    assert item["yoy"] == pytest.approx(.2)
    assert "120,000,000" in item["本期值"] and "億元" not in item["本期值"]
    assert "單位" in item["本期值"]


def test_mixed_known_units_are_normalized_without_changing_rates(tmp_path):
    rows = [revenue(2025, 9, 100000, revenue_unit="TWD"), revenue(2026, 9, 120, revenue_unit="新臺幣千元")]
    root, head = central(tmp_path, {"finmind_month_revenue": rows})
    item = metric(comparison.build_comparisons(["2330"], "20261016", central_root=root, head_sha=head)["2330"], "revenue_monthly")
    assert item["current_value"] == 120000
    assert item["yoy"] == pytest.approx(.2)


def test_signal_cutoff_future_visibility_and_new_pin_are_explicit(tmp_path):
    rows = [revenue(2025, 9, 100), revenue(2026, 8, 110),
            revenue(2026, 9, 999, available_time="2026-10-20T09:00:00+08:00")]
    root, head = central(tmp_path, {"finmind_month_revenue": rows})
    block = comparison.build_comparisons(["2330"], "20261016", central_root=root)["2330"]
    assert metric(block, "revenue_monthly")["資料期間"] == "202608"
    assert block["source_head_sha256"] == head
    assert block["source_pin_mode"] == "newly_pinned_for_comparison"
    assert block["historical_publication_verified"] is False
    assert any("最新" in note for note in block["notes"])


def test_corrupt_source_is_not_used_and_other_routes_survive(tmp_path):
    root, head = central(tmp_path, {"finmind_month_revenue": [revenue(2026, 9, 100)],
                                "finmind_per": [dict(stock_id="2330", date="20261015", per=20)]})
    manifest = json.loads((root / "_manifests" / (head + ".json")).read_text(encoding="utf-8"))
    path = root / manifest["datasets"][0]["path"]
    path.write_bytes(path.read_bytes().replace(b'100', b'900'))
    block = comparison.build_comparisons(["2330"], "20261016", central_root=root, head_sha=head)["2330"]
    assert not any(item["id"] == "revenue_monthly" for item in block["metrics"])
    assert metric(block, "valuation_per")["current_value"] == 20
    assert any("雜湊" in note for note in block["notes"])


def test_batch_streams_each_route_once_and_keeps_stock_identity(tmp_path, monkeypatch):
    rows = [revenue(2026, 9, 120), dict(revenue(2026, 9, 77), stock_id="3028")]
    root, head = central(tmp_path, {"finmind_month_revenue": rows})
    original, calls = comparison._verified_rows, []
    def counted(*args, **kwargs):
        calls.append(args[1]["route_id"])
        yield from original(*args, **kwargs)
    monkeypatch.setattr(comparison, "_verified_rows", counted)
    blocks = comparison.build_comparisons(["2330", "3028"], "20261016", central_root=root, head_sha=head)
    assert calls == ["finmind_month_revenue"]
    assert metric(blocks["3028"], "revenue_monthly")["current_value"] == 77
    assert metric(blocks["2330"], "revenue_monthly")["current_value"] == 120
    blocks["2330"]["metrics"].clear()
    repeated = comparison.build_comparisons(["2330"], "2026-10-16", central_root=root, head_sha=head)
    assert calls == ["finmind_month_revenue"]
    assert metric(repeated["2330"], "revenue_monthly")["current_value"] == 120


def test_stale_flow_margin_tdcc_and_financial_comparisons(tmp_path):
    flows = [dict(stock_id="2330", date=f"202609{d:02}", foreign_investors_net=d) for d in range(1, 21)]
    routes = {"finmind_institutional": flows,
              "finmind_margin": [dict(stock_id="2330", date="20260919", margin_purchase_balance=100),
                                 dict(stock_id="2330", date="20260920", margin_purchase_balance=80)],
              "tdcc_share_distribution_raw": [dict(證券代號="2330", 資料日期="20260911", 持股分級="15", **{"占集保庫存數比例%": "20"}),
                                              dict(證券代號="2330", 資料日期="20260918", 持股分級="15", **{"占集保庫存數比例%": "22"})],
              "finmind_financial_statements": [dict(stock_id="2330", date="20250630", type="Revenue", value=100, period_basis="year_to_date"),
                                               dict(stock_id="2330", date="20260630", type="Revenue", value=120, period_basis="year_to_date")]}
    root, head = central(tmp_path, routes)
    block = comparison.build_comparisons(["2330"], "20261016", central_root=root, head_sha=head)["2330"]
    assert metric(block, "institutional_foreign_5")["current_value"] == 90
    assert metric(block, "institutional_foreign_20")["current_value"] == 210
    assert metric(block, "margin_purchase_balance")["delta"] == -20
    assert metric(block, "tdcc_15")["delta"] == 2
    assert metric(block, "financial_Revenue_year_to_date")["yoy"] == pytest.approx(.2)
    assert "20260920" in metric(block, "institutional_foreign_5")["資料日期"]


def test_preview_and_summaries_are_pure_and_escape_html(tmp_path):
    root, head = central(tmp_path, {"finmind_month_revenue": [revenue(2026, 9, 120)]})
    block = comparison.build_comparisons(["2330"], "20261016", central_root=root, head_sha=head)["2330"]
    original = copy.deepcopy(block)
    rows = comparison.comparison_rows(block)
    assert list(rows[0]) == ["項目", "資料期間", "本期值", "比較", "解讀", "資料日期"]
    assert len(comparison.comparison_summary(block, max_items=2)) <= 2
    block["notes"].append("<script>來源</script>")
    html = comparison.comparison_html(block)
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert original["metrics"] == block["metrics"]


def test_stream_handles_multibyte_text_across_chunks_without_full_file_load(tmp_path):
    row = revenue(2026, 9, 120, 備註="來源資料" * 20000)
    root, head = central(tmp_path, {"finmind_month_revenue": [row]})
    block = comparison.build_comparisons(["2330"], "2026-10-16", central_root=root, head_sha=head)["2330"]
    assert metric(block, "revenue_monthly")["current_value"] == 120


def test_monthly_decrease_is_explained_and_notes_do_not_expose_head_digest(tmp_path):
    rows = [revenue(2025, 9, 100), revenue(2026, 8, 100), revenue(2026, 9, 80)]
    root, head = central(tmp_path, {"finmind_month_revenue": rows})
    block = comparison.build_comparisons(["2330"], "2026-10-16", central_root=root)["2330"]
    text = metric(block, "revenue_monthly")["解讀"]
    assert "較去年同月減少 20.00%" in text and "較上月減少 20.00%" in text
    assert head not in " ".join(block["notes"])
    assert any("單位一致假設" in note for note in block["notes"])


def test_summary_explains_increase_decrease_and_ytd_without_generic_warning():
    block = {"stock_id": "2330", "signal_date": "2026-10-16", "notes": [], "metrics": [
        {"id": "revenue_monthly", "current_value": 120, "year_ago_value": 100, "previous_value": 150,
         "unit": "單位未核實", "項目": "月營收", "資料期間": "202609", "本期值": "120（單位未核實）",
         "比較": "變化+20%/-20%", "解讀": "季節性、工作天與春節仍須考量。", "資料日期": "來源日 20261001；擷取日 20261017"},
        {"id": "revenue_ytd", "current_value": 900, "year_ago_value": 750, "unit": "單位未核實",
         "項目": "年初累計營收", "資料期間": "202601～202609", "本期值": "900（單位未核實）",
         "比較": "變化+20%", "解讀": "不據此判定獲利好壞。", "資料日期": "來源日 20261001"}]}
    summary = comparison.comparison_summary(block, max_items=2)
    assert "較去年同月增加20.00%" in summary[0]
    assert "較上月減少20.00%" in summary[0]
    assert "202609" in summary[0] and "資料日20261001" in summary[0]
    assert "較去年同期增加20.00%" in summary[1] and "202601～202609" in summary[1]
    assert "同源單位一致假設" in summary[0]
    assert not any("季節性" in line or "獲利好壞" in line for line in summary)
    assert "較去年同月增加20.00%" in comparison.comparison_html(block)


@pytest.mark.parametrize("fields,visible", [
    ({"date": "invalid"}, False),
    ({"date": "20261017"}, False),
    ({"date": "20261016"}, True),
    ({"date": "20261016", "available_time": "2026-10-16T23:59:59.999999+08:00"}, True),
    ({"date": "20261016", "available_time": "2026-10-16T15:59:59.999999Z"}, True),
    ({"date": "20261016", "available_time": "2026-10-16T16:00:00Z"}, False),
    ({"date": "20261016", "available_time": "invalid"}, True),
    ({"date": "20261016", "available_time": "invalid", "available_at": "2026-10-17"}, False),
    ({"date": "20261016", "available_time": "2026-10-16", "create_time": "2026-10-17"}, False),
])
def test_collector_keeps_visible_wrapper_boundaries(monkeypatch, fields, visible):
    row = dict(stock_id="2330", margin_purchase_balance=100, **fields)
    signal = comparison._day("20261016")
    assert comparison._visible(row, signal) is visible
    monkeypatch.setattr(comparison, "_verified_rows", lambda root, entry: iter([row]))
    grouped = comparison._collect(None, {"route_id": "finmind_margin"}, ("2330",), signal)
    assert grouped == {"2330": [row] if visible else []}


def test_collector_parses_business_dates_once_and_builds_cutoff_lazily(monkeypatch):
    rows = [dict(stock_id="2330", date=day) for day in ("20261015", "20261016", "20261017", "invalid")]
    rows.append(dict(stock_id="9999", date="20261016"))
    signal = comparison._day("20261016")
    expected = [row for row in rows if row["stock_id"] == "2330" and comparison._visible(row, signal)]
    original_day, original_datetime = comparison._day, comparison.datetime
    day_calls, cutoff_calls = [], []

    def counted_day(value):
        day_calls.append(value)
        return original_day(value)

    class CountedDateTime(original_datetime):
        @classmethod
        def combine(cls, *args, **kwargs):
            cutoff_calls.append(1)
            return original_datetime.combine(*args, **kwargs)

    monkeypatch.setattr(comparison, "_day", counted_day)
    monkeypatch.setattr(comparison, "datetime", CountedDateTime)
    monkeypatch.setattr(comparison, "_verified_rows", lambda root, entry: iter(rows))
    grouped = comparison._collect(None, {"route_id": "finmind_margin"}, ("2330",), signal)
    assert grouped == {"2330": expected}
    assert day_calls == ["20261015", "20261016", "20261017", "invalid"]
    assert cutoff_calls == [1]
    day_calls.clear()
    cutoff_calls.clear()
    monkeypatch.setattr(comparison, "_verified_rows", lambda root, entry: iter([dict(stock_id="2330", date="invalid")]))
    assert comparison._collect(None, {"route_id": "finmind_margin"}, ("2330",), None) == {"2330": []}
    assert day_calls == ["invalid"] and cutoff_calls == []


@pytest.mark.parametrize("route", ["finmind_institutional", "finmind_margin"])
def test_collector_keeps_availability_cutoff_after_flow_history_pruning(monkeypatch, route):
    rows = [dict(stock_id="2330", date=(comparison.date(2026, 1, 1) + comparison.timedelta(days=index)).strftime("%Y%m%d"),
                 available_time="2026-01-01T00:00:00+08:00") for index in range(102)]
    signal = comparison.date(2026, 10, 16)
    assert all(comparison._visible(row, signal) for row in rows)
    monkeypatch.setattr(comparison, "_verified_rows", lambda root, entry: iter(rows))
    grouped = comparison._collect(None, {"route_id": route}, ("2330",), signal)
    # Row 101 compacts to 40 days; row 102 must still pass the datetime availability gate.
    assert grouped == {"2330": rows[-41:]}


@pytest.mark.parametrize("max_items", [0, 1, 3, 50])
def test_combined_display_matches_legacy_full_rows_and_summary(tmp_path, monkeypatch, max_items):
    root, head = central(tmp_path, {"finmind_month_revenue": [revenue(2025, 9, 100), revenue(2026, 9, 120)]})
    block = comparison.build_comparisons(["2330"], "20261016", central_root=root, head_sha=head)["2330"]
    before = copy.deepcopy(block)
    expected = (comparison.comparison_rows(block), comparison.comparison_summary(block, max_items=max_items))
    original_rows, calls = comparison.comparison_rows, []

    def counted_rows(value):
        calls.append(value)
        return original_rows(value)

    monkeypatch.setattr(comparison, "comparison_rows", counted_rows)
    assert comparison.comparison_display(block, max_items=max_items) == expected
    assert len(calls) == 1 and block == before


def test_summary_and_combined_display_keep_late_metric_errors():
    class BadText:
        def __str__(self):
            raise ValueError("late metric conversion failed")

    row = {column: "value" for column in comparison.COLUMNS}
    block = {"metrics": [dict(row) for _ in range(3)] + [dict(row, **{"解讀": BadText()})]}
    with pytest.raises(ValueError, match="late metric conversion failed"):
        comparison.comparison_summary(block, max_items=3)
    with pytest.raises(ValueError, match="late metric conversion failed"):
        comparison.comparison_display(block, max_items=3)
    assert comparison.comparison_summary(block, max_items=0) == []
    with pytest.raises(ValueError, match="late metric conversion failed"):
        comparison.comparison_display(block, max_items=0)


def test_cache_returns_defensive_nested_blocks_for_hits_and_partial_misses(tmp_path):
    rows = [revenue(2026, 9, 120), dict(revenue(2026, 9, 77), stock_id="3028")]
    root, head = central(tmp_path, {"finmind_month_revenue": rows})
    comparison.build_comparisons(["2330"], "20261016", central_root=root, head_sha=head)
    blocks = comparison.build_comparisons(["2330", "3028"], "20261016", central_root=root, head_sha=head)
    expected = copy.deepcopy(blocks)
    blocks["2330"]["notes"].append("caller mutation")
    metric(blocks["2330"], "revenue_monthly")["current_value"] = -999
    metric(blocks["3028"], "revenue_3m")["missing_months"].append("999999")
    repeated = comparison.build_comparisons(["2330", "3028"], "20261016", central_root=root, head_sha=head)
    assert repeated == expected
    repeated["3028"]["metrics"].clear()
    assert comparison.build_comparisons(["2330", "3028"], "20261016", central_root=root, head_sha=head) == expected


@pytest.fixture
def projection(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "single_stock_analysis" / "src"))
    import stock_query_projection

    monkeypatch.setattr(stock_query_projection.cache_lifecycle, "source_registry",
                        lambda root: tmp_path / "projection_registry")
    return stock_query_projection


def _prepare_projections(projection, root, head, tmp_path):
    entries = comparison._head(root, head)[1]["datasets"]
    for entry in entries:
        result = projection.query_published_rows(
            root, entry, entry["route_id"], parent_identity=head,
            consumer_temp_root=tmp_path / "consumer", chunk_rows=2)
        result.close()
    return entries


def test_projection_opt_in_preserves_five_years_all_routes_order_duplicates_and_cutoff(
        tmp_path, monkeypatch, projection):
    revenue_rows = [revenue(y, m, y + m, revenue_unit="TWD")
                    for y in range(2021, 2027) for m in range(1, 10)]
    per = [dict(stock_id="2330", date=day, per=value) for day, value in [
        ("20261015", 20), ("20210101", 10), ("invalid", 999),
        ("20261015", 30), ("20261016", 22), ("20261017", 888)]]
    per += [dict(stock_id="2330", date="20261016", pbr=99,
                 available_time="2026-10-16T16:00:00Z")]
    routes = {"finmind_month_revenue": revenue_rows, "finmind_per": per,
              "finmind_institutional": [dict(stock_id="2330", date="20261015", foreign_investors_net=10)],
              "finmind_margin": [dict(stock_id="2330", date="20261015", margin_purchase_balance=100)],
              "tdcc_share_distribution_raw": [dict(stock_id="2330", date="20261015", holding_level="15",
                                                    **{"占集保庫存數比例%": 20})]}
    for route in ("finmind_financial_statements", "finmind_balance_sheet", "finmind_cash_flows_statement"):
        routes[route] = [dict(stock_id="2330", date="20260930", type="Revenue", value=100, unit="TWD")]
    for route, rows in routes.items():
        routes[route] = [dict(rows[0], stock_id="3028"), *rows, dict(rows[-1], stock_id="3028")]
    root, head = central(tmp_path, routes)
    entries = _prepare_projections(projection, root, head, tmp_path)
    baseline = comparison.build_comparisons(["2330"], "20261016", central_root=root, head_sha=head)
    original, calls, results = projection.query_published_rows, [], []

    def query(root_arg, entry, route, **kwargs):
        assert entry == next(item for item in entries if item["route_id"] == route)
        assert root_arg == root
        assert kwargs == dict(parent_identity=head, allow_build=False, stock_ids=("2330",),
                              start_date=None, end_date=None, before_count=0, columns=None,
                              consumer_temp_root=tmp_path / "consumer")
        calls.append(route)
        result = original(root_arg, entry, route, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(projection, "query_published_rows", query)
    monkeypatch.setattr(comparison, "_verified_rows", lambda *args: pytest.fail("Warm routes must not full-scan"))
    optimized = comparison.build_comparisons(
        ["2330"], "20261016", central_root=root, head_sha=head,
        use_verified_projection=True, consumer_temp_root=tmp_path / "consumer")
    assert optimized == baseline
    assert calls == list(comparison._ROUTES)
    assert all(result._closed and result._active_iterators == 0 for result in results)
    monthly = metric(optimized["2330"], "revenue_monthly")
    assert monthly["actual_history_start"] == "202101" and monthly["observed_months"] == 54
    assert metric(optimized["2330"], "revenue_same_month")["historical_years"] == list(range(2021, 2027))
    assert metric(optimized["2330"], "valuation_per")["current_value"] == 22
    entry = next(item for item in entries if item["route_id"] == "finmind_per")
    with comparison.closing(comparison._projection_rows(root, entry, ("2330",), head,
                                                       tmp_path / "consumer")) as rows:
        assert list(rows) == per  # Unknown dates and duplicate rows reach the unchanged collector.


@pytest.mark.parametrize("kind", ["alias", "missing", "unavailable"])
def test_projection_unavailable_or_lossy_stock_mapping_falls_back(tmp_path, monkeypatch, projection, kind):
    rows = [revenue(2025, 9, 100), revenue(2026, 9, 120)]
    if kind == "alias":
        rows = [dict(data_id=row.pop("stock_id"), **row) for row in rows]
    elif kind == "missing":
        rows.append(dict(證券代號="2330", date="20261001", revenue_year=2026, revenue_month=9, revenue=120))
    root, head = central(tmp_path, {"finmind_month_revenue": rows})
    if kind != "unavailable":
        _prepare_projections(projection, root, head, tmp_path)
    baseline = comparison.build_comparisons(["2330"], "20261016", central_root=root, head_sha=head)
    original, calls = comparison._verified_rows, []

    def verified(*args):
        calls.append(args[1]["route_id"])
        yield from original(*args)

    monkeypatch.setattr(comparison, "_verified_rows", verified)
    optimized = comparison.build_comparisons(
        ["2330"], "20261016", central_root=root, head_sha=head,
        use_verified_projection=True, consumer_temp_root=tmp_path / "consumer")
    assert optimized == baseline and calls == ["finmind_month_revenue"]


@pytest.mark.parametrize("stock_field,missing", [("data_id", 0), ("stock_id", 1),
                                                ("stock_id", None), ("stock_id", False)])
def test_projection_requires_explicit_lossless_stock_metadata(monkeypatch, projection, stock_field, missing):
    closed = []
    result = SimpleNamespace(field_map={"stock_field": stock_field}, manifest={"missing_stock_rows": missing},
                             iter_rows=lambda: pytest.fail("Unsafe mapping must not start iteration"),
                             close=lambda: closed.append("result"))
    monkeypatch.setattr(projection, "query_published_rows", lambda *args, **kwargs: result)
    original = [revenue(2026, 9, 120)]
    monkeypatch.setattr(comparison, "_verified_rows", lambda *args: iter(original))
    assert list(comparison._projection_rows(None, {"route_id": "finmind_month_revenue"},
                                           ("2330",), "a" * 64, None)) == original
    assert closed == ["result"]


@pytest.mark.parametrize("error", ["ProjectionUnavailableError", "UnsupportedProjectionError", "ProjectionIntegrityError"])
@pytest.mark.parametrize("after_row", [False, True])
def test_projection_failure_never_splices_rows_and_always_closes(monkeypatch, projection, error, after_row):
    closed, fallback = [], []

    def rows():
        try:
            if after_row:
                yield revenue(2026, 9, 999)
            raise getattr(projection, error)("fixture_failure")
        finally:
            closed.append("iterator")

    result = SimpleNamespace(field_map={"stock_field": "stock_id"}, manifest={"missing_stock_rows": 0},
                             iter_rows=rows, close=lambda: closed.append("result"))
    monkeypatch.setattr(projection, "query_published_rows", lambda *args, **kwargs: result)
    original = [revenue(2026, 9, 120)]

    def verified(*args):
        fallback.append(1)
        yield from original

    monkeypatch.setattr(comparison, "_verified_rows", verified)
    stream = comparison._projection_rows(None, {"route_id": "finmind_month_revenue"}, ("2330",), "a" * 64, None)
    if after_row or error == "ProjectionIntegrityError":
        with pytest.raises(getattr(projection, error), match="fixture_failure"):
            comparison._collect(None, {"route_id": "finmind_month_revenue"}, ("2330",),
                                comparison._day("20261016"), rows=stream)
        assert fallback == []
    else:
        assert list(stream) == original and fallback == [1]
    assert closed == ["iterator", "result"]


def test_projection_tamper_is_closed_not_a_full_scan_fallback(tmp_path, monkeypatch, projection):
    root, head = central(tmp_path, {"finmind_month_revenue": [revenue(2026, 9, 100)]})
    entry = _prepare_projections(projection, root, head, tmp_path)[0]
    result = projection.query_published_rows(root, entry, entry["route_id"], parent_identity=head,
                                             stock_ids=["2330"], allow_build=False)
    path = result.query_root / result.selected[0]["path"]
    result.close()
    raw = path.read_bytes()
    changed = raw.replace(b'"revenue":100', b'"revenue":900')
    assert changed != raw and len(changed) == len(raw)
    path.write_bytes(changed)
    monkeypatch.setattr(comparison, "_verified_rows", lambda *args: pytest.fail("Integrity errors must not fallback"))
    block = comparison.build_comparisons(
        ["2330"], "20261016", central_root=root, head_sha=head, use_verified_projection=True,
        consumer_temp_root=tmp_path / "consumer")["2330"]
    assert block["metrics"] == []
    assert any("shard" in note and "mismatch" in note for note in block["notes"])


def test_projection_opt_in_does_not_change_default_consumer(tmp_path, monkeypatch, projection):
    root, head = central(tmp_path, {"finmind_month_revenue": [revenue(2026, 9, 100)]})
    monkeypatch.setattr(projection, "query_published_rows", lambda *args, **kwargs: pytest.fail("Default must not query"))
    block = comparison.build_comparisons(["2330"], "20261016", central_root=root, head_sha=head)["2330"]
    assert metric(block, "revenue_monthly")["current_value"] == 100
