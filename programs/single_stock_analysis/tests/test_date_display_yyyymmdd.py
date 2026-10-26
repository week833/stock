"""The retained display helper keeps machine dates and timestamps distinct."""
from datetime import date
import date_format as cd

def test_display_date_prints_yyyymmdd_and_leaves_other_text():
    assert cd.display_day('2026-10-08') == '20261008'
    assert cd.display_day('20261008') == '20261008'
    assert cd.display_day(date(2026, 10, 8)) == '20261008'
    # shared date_format.display_day documents "-" as its default empty text; callers pass their own wording
    # (tw_single_stock_c0 passes empty='未知').
    assert cd.display_day(None) == '-'
    assert cd.display_day(None, empty='未取得') == '未取得'
    assert cd.display_day('', empty='未知') == '未知'
    assert cd.parse_day('20261008').isoformat() == '2026-10-08'
    assert cd.display_text('資料日 2026-10-08') == '資料日 20261008'
