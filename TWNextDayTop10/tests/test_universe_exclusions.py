"""Universe exclusions: preferred shares, warrants, ETF codes and 興櫃 never enter the model."""
from data_reader import build_universe


def _ordinary_rows():
    return [dict(stock_id=code, market=market, date="2026-09-20", stock_name=name,
                 industry_category="半導體業", security_type="ordinary_share", classification_verified=True,
                 classification_source="official_fixture", classification_effective_date="2026-09-01")
            for code, market, name in [("2330", "TWSE", "台積電"), ("AB123", "TPEX", "合格KY創新板樣本")]]


def _row(code, name, market="TWSE", date="2026-09-20", industry="金融保險"):
    return dict(_ordinary_rows()[0], stock_id=code, stock_name=name, market=market, date=date,
                industry_category=industry)


def test_preferred_shares_warrants_and_zero_prefixed_codes_are_excluded():
    rows = _ordinary_rows() + [
        _row("2881B", "富邦金乙特"), _row("2881A", "富邦特"), _row("2887E", "台新新光戊特一"),
        _row("2833A", "台壽甲"), _row("710553", "穩懋統一9B購03", "TPEX", industry="所有證券"),
        _row("73107P", "原相國票9B售02", "TPEX", industry="所有證券"), _row("00866", "新光Shiller CAPE", industry=""),
        _row("3289", "宜特", industry="其他電子類"), _row("2941", "米斯特", industry="居家生活類"),
    ]
    kept = set(build_universe(rows).stock_id)
    assert kept == {"2330", "AB123", "3289", "2941"}


def test_code_back_on_emerging_board_is_not_listed_universe():
    rows = _ordinary_rows() + [
        _row("6999", "回興櫃樣本", "TPEX", date="2026-09-01", industry="半導體業"),
        _row("6999", "回興櫃樣本", "EMERGING", date="2026-09-20", industry="半導體業"),
        _row("6998", "新上櫃樣本", "EMERGING", date="2026-09-01", industry="半導體業"),
        _row("6998", "新上櫃樣本", "TPEX", date="2026-09-20", industry="半導體業"),
        _row("6997", "興櫃樣本", "EMERGING", industry="半導體業"),
    ]
    universe = build_universe(rows)
    assert "6999" not in set(universe.stock_id)
    assert "6997" not in set(universe.stock_id)
    assert universe.loc[universe.stock_id.eq("6998"), "market"].tolist() == ["TPEX"]
