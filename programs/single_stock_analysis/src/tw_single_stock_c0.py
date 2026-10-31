"""Single-stock C0 close-to-close estimates from the shared active model and published inputs.

The analysis uses the data actually read (one pin for the whole run) and every
report states that data's time (``data_time``).  Version differences are
``version_labels``; only integrity/content errors stop the run (rc=2).
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import html
import json
import math
import os
import sys
from contextlib import nullcontext
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

SHARED=Path(__file__).resolve().parents[2]/'shared'
if str(SHARED) not in sys.path:sys.path.insert(0,str(SHARED))
import stock_daily_contract as contract
import stock_daily_selected_deployment as selected
import date_format as cd
import single_stock_preopen as preopen
from report_comparison_bridge import attach_report_comparison, report_comparison_rows, comparison_preview_html

REPORT_SCHEMA=contract.C0_REPORT_SCHEMA
DEFAULT_OUTPUT=Path(r'D:\TWStockData\program_data\dstock_single\reports\c0')
NO_ESTIMATE_CLASS='c0_no_estimate_data_time_only'
# Same canonical published root as contract._current_c0_sources(); a caller's job
# pin names one immutable head _manifests/<sha>.json under it.
PUBLISHED_ROOT=Path(r'D:\TWStockData\shared_market_data')
PINNED_HEAD_UNAVAILABLE='pinned_head_unavailable_used_current'
# Panel exclusions of the requested stock: content verdicts (the stock's own
# data), reported as a no-estimate report instead of a failed run.
_STOCK_EXCLUSION_PREFIXES=('c0_no_stock_passed_current_price_scope_quality',
    'c0_no_stock_with_verified_current_identity_and_exact_market_calendar')
_REASON_ZH={'stock_price_older_than_asof':'該股價格資料停在分析資料日之前','horizon_not_trained':'模型未訓練此期限',
    'latest_exact_253_market_sessions_incomplete':'最近253個交易日價格不完整',
    'model_prediction_unavailable':'現有模型無法產生有效估計','prediction_unavailable':'現有模型未產生有效估計',
    'price_data_unavailable':'本次沒有可用的有效價格資料',
    'current_official_classification_unknown':'目前官方普通股分類未知（排除）',
    'market_calendar_applicability_or_known_range_unverified':'市場交易日曆未驗證',
    'market_calendar_exact_253_sessions_unavailable':'已驗證日曆不足253個交易日',
    'market_asof_differs_from_inference_asof':'該市場資料日與分析資料日不同',
    'price_provenance_observation_missing':'價格來源觀測時間缺漏','duplicate_price_key':'價格資料重複',
    'price_row_outside_exact_verified_market_axis':'價格日期不在已驗證交易日內'}


def _json_value(value):
    if hasattr(value,'item'):return value.item()
    if hasattr(value,'isoformat'):return value.isoformat()
    raise TypeError(type(value).__name__)


def _canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False,default=_json_value).encode('utf-8')


def _clean(value):
    """JSON-safe copy for a row that carries no estimate (NaN -> None, numpy -> python)."""
    if isinstance(value,dict):return {str(k):_clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [_clean(v) for v in value]
    if hasattr(value,'item') and not isinstance(value,(str,bytes)):
        try:value=value.item()
        except (TypeError,ValueError):return str(value)
    if isinstance(value,float) and not math.isfinite(value):return None
    if hasattr(value,'isoformat') and not isinstance(value,str):return value.isoformat()
    try:
        if value!=value:return None  # NaT/NA
    except (TypeError,ValueError):
        pass
    return value


def _now_taipei():
    return datetime.now(ZoneInfo('Asia/Taipei'))


def _merge_labels(*groups):
    merged,seen=[],set()
    for group in groups:
        for label in group or []:
            key=_canonical(label)
            if key not in seen:seen.add(key);merged.append(copy.deepcopy(label))
    return merged


def _data_time(source,loaded):
    value=source.get('data_time')
    if isinstance(value,dict):return copy.deepcopy(value)
    return contract.build_data_time(source,model=loaded)


STOCK_MARKET_ASOF_LAGS='stock_market_asof_lags_latest_market'


def _market_key(by_market,market):
    """The key of ``market`` in a per-market mapping (case-insensitive: TPEx/TPEX), or None."""
    wanted=str(market or '').upper()
    for key in by_market or {}:
        if str(key).upper()==wanted:return key
    return None


def _stock_market_asof(source,market):
    """The analysed day of the stock's OWN market (core ``asof_by_market``; else the one ``asof``).

    A context carries ``analysis_asof_by_market`` (its ``asof_by_market`` are
    the scope days, possibly after the price data); a load_c0_published_panel
    result carries ``asof_by_market`` (each market's analysed day).  Inputs
    without either (one common day) use ``asof`` unchanged.
    """
    for name in ('analysis_asof_by_market','asof_by_market'):
        by_market=source.get(name)
        if isinstance(by_market,dict) and by_market:
            key=_market_key(by_market,market)
            if key is not None and by_market[key]:return str(by_market[key])[:10]
            break
    return source['asof']


def _stock_market_lag_label(data_time,market,stock_asof):
    """A label when the stock's market is analysed at an earlier day than the latest market (estimated, not excluded)."""
    by_market={str(k):str(v)[:10] for k,v in (data_time.get('asof_by_market') or {}).items() if v}
    latest=max(by_market.values(),default=None)
    if latest is None or not stock_asof or stock_asof>=latest:return None
    return {'code':STOCK_MARKET_ASOF_LAGS,'recorded':latest,'actual':stock_asof,
        'detail':{'market':str(market).upper(),'asof_by_market':by_market,'analysis':'estimated_with_own_market_asof'}}


def _stock_data_time(data_time,market,stock_asof,lag_label=None):
    """``data_time`` as seen by ONE stock: its own market's analysed day and freshness.

    The whole-data values (latest day over all markets, any-market staleness)
    are kept unchanged under ``all_markets``; ``stale_markets`` and
    ``asof_by_market`` stay per market.  A stock whose market is current is not
    reported stale because another market lags, and a stock in a lagging market
    is reported with its own (earlier) day.
    """
    value=copy.deepcopy(data_time or {})
    value['all_markets']={key:copy.deepcopy(value.get(key)) for key in ('analysis_asof','is_stale','freshness_status',
        'latest_expected_session','is_older_than_requested')}
    expected_key=_market_key(value.get('latest_expected_session_by_market'),market)
    expected=(value.get('latest_expected_session_by_market') or {}).get(expected_key) if expected_key is not None else value.get('latest_expected_session')
    stale=bool(expected and stock_asof and stock_asof<expected)
    requested=value.get('requested_asof')
    older=bool(requested and stock_asof and stock_asof<requested)
    by_market={str(k):str(v)[:10] for k,v in (value.get('asof_by_market') or {}).items() if v}
    # Unknown market (e.g. identity not classified): None, never the text 'NONE'.
    value.update({'analysis_asof':stock_asof,'stock_market':str(market).upper() if market else None,'stock_market_asof':stock_asof,
        'latest_market_asof':max(by_market.values(),default=stock_asof),'stock_market_asof_lags':lag_label is not None,
        'latest_expected_session':expected,'is_stale':stale,'is_older_than_requested':older,
        'freshness_status':'stale' if stale else 'calendar_unverified_after' if value.get('weekdays_after_calendar') else 'latest'})
    if lag_label is not None:value['labels']=_merge_labels(value.get('labels'),[lag_label])
    return value


def _runtime_binding(inputs,loaded):
    """Same fields as contract.c0_runtime_binding, from the already pinned inputs (no second context load)."""
    descriptor=inputs['descriptor']
    return {'schema':'stock-daily-c0-runtime-binding-v2','asof':inputs['asof'],
        'snapshot_fingerprint':descriptor['snapshot_fingerprint'],'published_sources':descriptor['sources'],
        'identity_binding':inputs['current_identity']['binding'],
        'calendar_binding':inputs['calendar_evidence']['binding'],
        'calendar_verified_by_market':inputs['calendar_evidence']['calendar_verified_by_market'],
        'application_scope_binding':inputs['application_scope']['binding'],
        'capture_scope_binding':inputs['application_scope']['capture_binding'],
        'required_route_evidence':inputs['required_route_evidence'],'readiness':inputs['readiness'],
        'model_binding':loaded['artifact_binding'],'pointer_binding':loaded['pointer_binding'],
        'policy_binding':loaded['policy_binding'],**contract._dual_bindings(loaded)}


def _pinned_head_reference(pinned_head_sha256):
    """The caller's immutable-head pin as a context_or_sources mapping, or None for the current head.

    An invalid sha is a caller error (ValueError).  A head that no longer exists
    is not an error: the run uses the current head and says so in a label.  An
    existing head whose bytes do not hash to the sha still raises (integrity).
    """
    if pinned_head_sha256 is None:return None,[]
    import re
    sha=str(pinned_head_sha256).strip().lower()
    if not re.fullmatch('[0-9a-f]{64}',sha):raise ValueError('c0_pinned_head_sha256_invalid')
    path=PUBLISHED_ROOT/'_manifests'/(sha+'.json')
    try:
        raw=path.read_bytes()
    except FileNotFoundError:
        return None,[_pinned_head_unavailable_label(sha,'pinned_head_manifest_missing')]
    # Content-addressed: bytes that do not hash to the name are tamper (KEEP), never a fallback.
    if hashlib.sha256(raw).hexdigest()!=sha:raise contract.C0IntegrityError('daily_immutable_head_digest_mismatch')
    return {'root':str(PUBLISHED_ROOT),'manifest_path':str(path),'manifest_sha256':sha},[]


def _pinned_head_unavailable_label(sha,reason):
    return {'code':PINNED_HEAD_UNAVAILABLE,'recorded':{'head_sha256':sha},'actual':None,
        'detail':{'reason':reason,'analysed':'current_head'}}


def _with_labels(source,labels):
    """Add pin labels to a loaded context/inputs mapping (its version_labels and data_time.labels)."""
    if labels:
        source['version_labels']=_merge_labels(labels,source.get('version_labels'))
        if isinstance(source.get('data_time'),dict):
            source['data_time']['labels']=_merge_labels(labels,source['data_time'].get('labels'))
    return source


def _load_pinned_context(loaded,pinned_head_sha256):
    """(context, pin labels): the caller's pinned head when it still exists, else the current head (labelled).

    The labels are returned too because a later load from this context (the
    panel) rebuilds its own label list.
    """
    reference,labels=_pinned_head_reference(pinned_head_sha256)
    if reference is not None:
        try:
            context=contract.load_c0_published_context(reference,consumer_id='single',runtime_binding=loaded)
        except FileNotFoundError as exc:
            # The head (or a file it references) was removed after the existence check: same as missing.
            reference,labels=None,[_pinned_head_unavailable_label(reference['manifest_sha256'],
                'pinned_head_or_inputs_missing:'+str(exc)[:200])]
        else:
            return context,labels
    context=contract.load_c0_published_context(consumer_id='single',runtime_binding=loaded)
    return _with_labels(context,labels),labels


def _resolve(query,identity,max_matches):
    text=query.strip().casefold();matches=[]
    for record in identity['records']:
        code=str(record['stock_id']);name=str(record.get('stock_name') or '')
        score=1.0 if text in {code.casefold(),name.casefold()} else 0.8 if text and text in name.casefold() else 0.0
        if score:matches.append(dict(record,match_type='exact' if score==1 else 'contains',match_score=score))
    matches.sort(key=lambda r:(-r['match_score'],str(r['stock_id'])))
    return matches[:max_matches] if max_matches else matches


def _emit_phase(stock,phase):
    # The normal Telegram child recognises the same bounded phase envelope.
    import os
    if os.environ.get('DSTOCK_SINGLE_STOCK_PHASE_PROGRESS')=='1':
        print('[SINGLE_STOCK_PHASE] '+json.dumps({'schema':'single-stock-progress-v1','stock_id':stock,'phase':phase},ensure_ascii=False),flush=True)


def _day(value):
    return cd.display_day(value,empty='未知')


def _clock(value):
    return cd.display_clock(value,empty='未知')


def _data_time_summary(data_time):
    """One console line (no '｜', so stock-resolution parsers ignore it)."""
    markets='／'.join(f"{market} {_day(day)}" for market,day in sorted((data_time.get('asof_by_market') or {}).items()))
    stale='；非最新資料' if data_time.get('is_stale') or data_time.get('is_older_than_requested') else ''
    return (f"資料時間：分析資料日 {_day(data_time.get('analysis_asof'))}（{markets or '未知'}）；價格最新日 {_day(data_time.get('price_max_date'))}；"
        f"發布時間（發布者宣告）{_clock((data_time.get('head') or {}).get('published_at'))}；讀取 {_clock(data_time.get('read_at_utc'))}{stale}")


_REPORT_LABEL_ZH = {
    'inference_code_differs_from_training':'分析程式與模型訓練時使用的程式不同',
    'mutable_ref_changed_since_adoption':'部分來源參照已更新',
    'contract_adoption_unverified':'目前契約採納狀態尚未驗證',
    'adoption_unverified':'採納紀錄尚未驗證',
    'price_lags_scope':'價格資料日早於更新範圍截止日',
    'current_classification_observed_after_data_asof':'普通股分類觀測日較分析資料日晚',
    'market_asof_differs':'各市場的資料日期不同',
    'market_asof_differs_per_market_inference':'各市場依自己的資料日分析',
    'stock_market_asof_lags_latest_market':'本股依所屬市場的資料日分析',
    'pinned_head_unavailable_used_current':'原指定快照已不可讀，改採目前已發布資料',
    'optional_route_unavailable':'部分補充資料來源未提供',
    'requested_asof_differs':'實際分析資料日與要求日期不同',
}
_REPORT_METHOD_ZH = {
    'fit_up_rate_baseline':'訓練樣本整體上漲比率基準',
    'zero_return_baseline':'零報酬基準',
    'logistic':'邏輯斯方向模型',
    'ridge':'嶺迴歸報酬模型',
}
_REPORT_CALIBRATION_ZH = {
    'uncalibrated_constant_fit_up_rate':'採整體上漲比率基準，未另作機率校準',
    'uncalibrated_no_forward_overlay':'尚無前瞻資料校正',
    'independent_calibration_fitted_reliability_unverified':'已做獨立樣本校準，可靠性尚未驗證',
    'uncalibrated':'尚未校準',
}
_REPORT_ROUTE_ZH = {
    'official_calendar':'市場交易日曆', 'official_market_daily':'官方每日行情',
    'finmind_stock_info':'股票基本識別資料', 'finmind_price_adj':'調整後股價',
    'finmind_dividend':'股利公告', 'finmind_dividend_result':'除權息結果',
}
_REPORT_STATUS_ZH = {
    'SUCCESS':'完成', 'COMPLETE':'完成', 'PARTIAL':'部分資料',
    'EMPTY':'無資料', 'FAILED':'未完成', 'BLOCKED':'條件未滿足',
    'UNAVAILABLE':'未提供', 'UNKNOWN':'尚未確認', 'WAITING_QUOTA':'等待資料額度',
}


def _market_zh(value):
    return {'TWSE':'上市','TPEX':'上櫃'}.get(str(value or '').upper(),'市場未提供')


def _report_float(value):
    try:
        number=float(value)
    except (TypeError,ValueError):
        return None
    return number if math.isfinite(number) else None


def _report_number(value,digits=2,*,percent=False,signed=False):
    number=_report_float(value)
    if number is None:return '未提供'
    if percent:number*=100
    return format(number,('+' if signed else '')+',.'+str(digits)+'f')+('%' if percent else '')


def _data_time_lines(payload):
    """Human-readable dates from the data actually read; machine values stay untouched."""
    data_time=payload.get('data_time') or {};head=data_time.get('head') or {};model=data_time.get('model') or {}
    markets='／'.join(f"{_market_zh(market)} {_day(day)}" for market,day in sorted((data_time.get('asof_by_market') or {}).items()))
    lines=[f"分析資料日：{_day(data_time.get('analysis_asof'))}；各市場：{markets or '未提供'}。",
        f"價格最新日：{_day(data_time.get('price_max_date'))}；本股最後價格日：{_day(payload.get('stock_last_price_date'))}。",
        f"發布時間（發布者宣告，尚未獨立驗證）：{_clock(head.get('published_at'))}。",
        f"讀取時間：{_clock(data_time.get('read_at_utc'))}；報告產生時間：{_clock(payload.get('generated_at'))}。",
        f"模型訓練資料日：{_day(model.get('training_asof'))}；模型啟用時間：{_clock(model.get('activated_at_utc'))}。"]
    if data_time.get('stock_market_asof_lags'):
        lines.append(f"本股所屬{_market_zh(data_time.get('stock_market'))}市場資料日 {_day(data_time.get('stock_market_asof'))}，早於其他市場最新日 {_day(data_time.get('latest_market_asof'))}；本股依自己的市場資料日估計。")
    if data_time.get('is_stale'):
        lines.append(f"【非最新資料】分析資料日早於已驗證日曆的最近交易日 {_day(data_time.get('latest_expected_session'))}。")
    others={market:days for market,days in (data_time.get('stale_markets') or {}).items()
            if data_time.get('stock_market') and str(market).upper()!=data_time.get('stock_market')}
    if others:
        lines.append('其他市場資料較舊（不影響本股）：'+'；'.join(f"{_market_zh(market)} {_day((days or [None])[0])}，最近交易日 {_day((days or [None,None])[-1])}" for market,days in sorted(others.items()))+'。')
    if data_time.get('is_older_than_requested'):
        lines.append(f"【非最新資料】分析資料日早於要求的 {_day(data_time.get('requested_asof'))}。")
    unverified=data_time.get('weekdays_after_calendar') or []
    if not data_time.get('is_stale') and data_time.get('freshness_status')=='calendar_unverified_after' and unverified:
        lines.append(f"已驗證日曆只到 {_day(data_time.get('calendar_axis_end'))}；其後至 {_day(unverified[-1])} 的 {len(unverified)} 個平日尚無法確認是否為交易日，因此無法確認是否最新。")
    if payload.get('in_sample_lookahead'):
        lines.append('【樣本內】分析資料日不晚於模型訓練資料日；此估計不計入前瞻統計。')
    labels=payload.get('version_labels') or []
    if labels:
        descriptions=list(dict.fromkeys(_REPORT_LABEL_ZH.get(str(item.get('code')),'其他資料或程式差異，詳見隨附資料檔') for item in labels if isinstance(item,dict)))
        if descriptions:lines.append('資料與程式差異說明：'+'；'.join(descriptions)+'。')
    return lines


@lru_cache(maxsize=1)
def _report_fonts():
    """Embed existing Traditional-Chinese TrueType faces once per process."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    fonts=Path(r'C:\Windows\Fonts')
    regular,bold='DStockTCRegular','DStockTCBold'
    pdfmetrics.registerFont(TTFont(regular,str(fonts/'msjh.ttc'),subfontIndex=0))
    pdfmetrics.registerFont(TTFont(bold,str(fonts/'msjhbd.ttc'),subfontIndex=0))
    pdfmetrics.registerFontFamily(regular,normal=regular,bold=bold,italic=regular,boldItalic=bold)
    return regular,bold


def _build_report_view(panel,stock_id,asof,market_sessions=None):
    """One in-memory view of the already validated panel, shared by both horizons."""
    frame=panel.loc[panel['stock_id'].astype(str)==str(stock_id)].copy()
    day_field='session_date' if 'session_date' in frame else 'date'
    frame['_report_day']=frame[day_field].map(cd.iso_calendar_day)
    frame=frame.loc[frame['_report_day'].notna()&(frame['_report_day']<=asof)].sort_values('_report_day')
    if frame.empty:return None
    if market_sessions and 'market' in frame:
        market=str(frame['market'].iloc[0]).upper()
        axis=next((days for key,days in market_sessions.items() if str(key).upper()==market),[])
        days=[day for value in axis if (day:=cd.iso_calendar_day(value)) and frame['_report_day'].iloc[0]<=day<=asof]
        if days:frame=frame.set_index('_report_day').reindex(days).rename_axis('_report_day').reset_index()
    price_field='adjusted_close' if 'adjusted_close' in frame else 'close'
    close=[_report_float(value) for value in frame[price_field]]
    close=[value if value is not None and value>0 else None for value in close]
    if not any(value is not None for value in close):return None
    volume=[_report_float(value) for value in frame['Trading_Volume']] if 'Trading_Volume' in frame else [None]*len(close)
    prices=__import__('pandas').Series(close,dtype=float)
    averages={period:prices.rolling(period).mean().tolist() for period in (5,20,60)}
    averages={period:[_report_float(value) for value in values] for period,values in averages.items()}
    changes=prices.pct_change(fill_method=None)
    volatility=_report_float(changes.tail(20).std(ddof=1)) if len(changes)>=21 and changes.tail(20).notna().all() else None
    returns={period:close[-1]/close[-period-1]-1 if len(close)>period and close[-1] is not None and close[-period-1] is not None else None for period in (5,20,60)}
    return {'stock_id':str(stock_id),'dates':frame['_report_day'].tolist(),'close':close,'volume':volume,
        'moving_averages':averages,'returns':returns,'volatility_20':volatility,
        'daily_change':close[-1]/close[-2]-1 if len(close)>1 and close[-1] is not None and close[-2] is not None else None}


def _report_price_chart(view,font_name):
    """A reusable vector chart; no data access or prediction occurs here."""
    if '_price_chart' in view:return view['_price_chart']
    from reportlab.graphics.shapes import Circle,Drawing,Line,PolyLine,Rect,String
    from reportlab.lib.colors import HexColor
    drawing=Drawing(500,290)
    left,right,bottom,top=52,488,102,246
    palette={0:'#2563EB',5:'#D97706',20:'#059669',60:'#7C3AED'}
    series={0:view['close'],**view['moving_averages']}
    values=[value for items in series.values() for value in items if value is not None]
    low,high=min(values),max(values);padding=max((high-low)*0.06,high*0.01,0.1)
    low,high=low-padding,high+padding
    count=len(view['dates']);width=right-left
    x=lambda index:left+width*index/max(count-1,1)
    y=lambda value:bottom+(top-bottom)*(value-low)/(high-low)
    def label(px,py,text,*,size=8,color='#475569',anchor='start'):
        drawing.add(String(px,py,text,fontName=font_name,fontSize=size,fillColor=HexColor(color),textAnchor=anchor))
    label(left,276,'調整後收盤價與均線（新臺幣元）',size=10,color='#0F172A')
    for index,(period,color) in enumerate(palette.items()):
        px=left+index*107;drawing.add(Line(px,261,px+15,261,strokeColor=HexColor(color),strokeWidth=1.5))
        label(px+20,258,'收盤價' if period==0 else f'{period}日均線')
    for step in range(5):
        value=low+(high-low)*step/4;py=y(value)
        drawing.add(Line(left,py,right,py,strokeColor=HexColor('#E2E8F0'),strokeWidth=0.5))
        label(left-7,py-3,_report_number(value,1),anchor='end')
    for period,items in series.items():
        points=[]
        for index,value in enumerate(items+[None]):
            if value is not None:
                points.extend((x(index),y(value)));continue
            if len(points)>=4:drawing.add(PolyLine(points,strokeColor=HexColor(palette[period]),strokeWidth=1.2 if period else 1.7,fillColor=None))
            elif points:drawing.add(Circle(*points,1.5,fillColor=HexColor(palette[period]),strokeColor=None))
            points=[]
    for index in sorted({0,(count-1)//3,2*(count-1)//3,count-1}):
        label(x(index),87,_day(view['dates'][index]),size=7.5,anchor='middle')
    volumes=[value if value is not None and value>=0 else None for value in view['volume']]
    maximum=max((value for value in volumes if value is not None),default=0)
    label(left,70,'原始成交量（千股）')
    if maximum>0:
        bar_width=max(0.5,width/max(count,1)*0.7)
        for index,value in enumerate(volumes):
            if value is not None:drawing.add(Rect(x(index)-bar_width/2,22,bar_width,39*value/maximum,fillColor=HexColor('#94A3B8'),strokeColor=None))
        label(left-7,55,_report_number(maximum/1000,0),anchor='end')
    else:label((left+right)/2,37,'本區間成交量為零' if any(value is not None for value in view['volume']) else '成交量未提供',anchor='middle')
    drawing.add(Line(left,22,right,22,strokeColor=HexColor('#CBD5E1'),strokeWidth=0.5))
    view['_price_chart']=drawing
    return drawing


def _preopen_block(payload):
    """The sealed H1 pre-open block of an H1 report, or None (H63 and older reports have none)."""
    block=payload.get('preopen_h1')
    return block if int(payload.get('horizon_sessions') or 0)==1 and isinstance(block,dict) else None


def _preopen_fields(stock,stock_asof,h1_source,target=None):
    """H1 only: the pre-open block (fresh snapshot row or a labelled C0 fallback); C0 machine fields stay unchanged.

    h1_source=c0 is the old behaviour: no block at all, so the report is the same as before this overlay.
    """
    if preopen.resolve_source(h1_source)=='c0':return {}
    block=preopen.build_h1_block(stock,stock_asof,source=h1_source,base_label=preopen.C0_BASE_LABEL,
        consumer='single_c0',base_target_date=target)
    return {'preopen_h1':block,'h1_display_source':block['display_source']}


def _preopen_rows(block):
    rank,universe=block.get('rank'),block.get('universe_rows')
    tradable_rank,tradable_rows=block.get('rank_tradable'),block.get('tradable_rows')
    if block.get('tradable') is True and isinstance(tradable_rank,int):
        tradable=f'第 {tradable_rank} 名／共 {tradable_rows} 檔'+('（可交易 Top10）' if tradable_rank<=10 else '')
    else:tradable='不在可交易母體（20 日均成交金額未達 2,000 萬元或收盤低於 10 元）'
    rows=[('H1 主要來源','開盤前版（HGB，含前一晚美股）'),
        ('上漲機率（開盤前版，校準後）',_report_number(block.get('p_up'),percent=True)),
        ('預估收盤報酬（開盤前版，校準後）',_report_number(block.get('exp_ret'),percent=True,signed=True)),
        ('全市場名次',f'第 {rank} 名／共 {universe} 檔' if isinstance(rank,int) and isinstance(universe,int) else '未提供'),
        ('可交易母體名次',tradable),
        ('前一晚美股',f"{block.get('gate_us_up_zh') or '前一晚美股漲跌未知'}；{block.get('us_overnight_status_zh') or '美股狀態未知'}"),
        ('訊號日至目標日',f"{_day(block.get('signal_date'))} 收盤至 {_day(block.get('target_date'))} 收盤"),
        ('模型說明',str(block.get('describe') or block.get('model_label') or '開盤前版'))]
    if block.get('fixture') is True:rows.append(('資料性質','測試用假快照，不可作為正式資訊'))
    if block.get('target_date_consistent') is False:
        rows.append(('日期提醒','開盤前版目標日與 C0 日曆推得的到期交易日不同，請以各自來源核對'))
    return rows


def _pdf(path,payload,report_view=None):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak
    regular,bold=_report_fonts()
    width=507
    styles={
        'title':ParagraphStyle('ReportTitle',fontName=bold,fontSize=20,leading=28,textColor=colors.HexColor('#0F172A'),spaceAfter=8,wordWrap='CJK'),
        'subtitle':ParagraphStyle('ReportSubtitle',fontName=regular,fontSize=10,leading=16,textColor=colors.HexColor('#475569'),spaceAfter=14,wordWrap='CJK'),
        'heading':ParagraphStyle('ReportHeading',fontName=bold,fontSize=13,leading=20,textColor=colors.HexColor('#0F4C81'),spaceBefore=14,spaceAfter=7,keepWithNext=True,wordWrap='CJK'),
        'body':ParagraphStyle('ReportBody',fontName=regular,fontSize=10,leading=17,spaceAfter=6,wordWrap='CJK'),
        'small':ParagraphStyle('ReportSmall',fontName=regular,fontSize=8.5,leading=14,textColor=colors.HexColor('#475569'),spaceAfter=5,wordWrap='CJK'),
        'notice':ParagraphStyle('ReportNotice',fontName=bold,fontSize=10.5,leading=17,textColor=colors.HexColor('#B91C1C'),spaceAfter=10,wordWrap='CJK'),
    }
    def paragraph(text,style='body'):
        return Paragraph(html.escape(str(text)).replace('\n','<br/>'),styles[style])
    story=[]
    def text(value,style='body'):story.append(paragraph(value,style))
    def section(number,title):text(f'{number}、{title}','heading')
    def table(rows):
        cells=[[paragraph('項目'),paragraph('內容')]]+[[paragraph(name),paragraph(value)] for name,value in rows]
        item=Table(cells,colWidths=[142,width-142],repeatRows=1,hAlign='LEFT')
        item.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#E8F0F8')),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F8FAFC')]),
            ('BOX',(0,0),(-1,-1),0.5,colors.HexColor('#CBD5E1')),
            ('INNERGRID',(0,0),(-1,-1),0.35,colors.HexColor('#E2E8F0')),
            ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),
            ('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        story.extend([item,Spacer(1,6)])
    stock=payload['stock'];h=int(payload['horizon_sessions'])
    estimated=payload.get('estimate_status')!='no_estimate'
    pred=payload.get('prediction') or {};metrics=payload.get('comparison_metrics') or {}
    duration='下一個市場交易日' if h==1 else f'{h}個市場交易日'
    scope_note='訊號日收盤至同市場下一個交易日收盤' if h==1 else f'訊號日收盤至同市場第{h}個交易日收盤'
    notes=payload.get('stock_data_notes') or []
    stock_name=(stock.get('stock_name') or '')+('〔資料不完整，可信度較低〕' if notes else '')
    unavailable='無法計算（資料不足）'
    def value(number,**options):
        return _report_number(number,**options) if _report_float(number) is not None else unavailable
    preopen_h1=_preopen_block(payload);preopen_used=bool(preopen_h1 and preopen_h1.get('status')=='used')
    # C0 labels gain a "backup" prefix only when the pre-open row is the H1 primary display.
    base='C0 基準（備援）' if preopen_used else ''
    text(f"{stock['stock_id']} {stock_name} 個股分析報告",'title')
    if preopen_used:text(f'H1 開盤前版（主要顯示）＋ C0 基準（備援）｜{duration}收盤估計｜{_market_zh(stock.get("market"))}','subtitle')
    else:text(f'現行 C0 模型｜H{h}｜{duration}收盤估計｜{_market_zh(stock.get("market"))}','subtitle')
    if preopen_h1 and not preopen_used:
        text('【注意】'+str(preopen_h1.get('notice'))+'。'+('原因：'+str(preopen_h1.get('reason_zh') or '開盤前版不可用')+'。'
            if preopen_h1.get('status')=='fallback' else ''),'notice')
    if notes:text('資料備註：'+'；'.join(notes),'small')
    section('一','摘要')
    if preopen_used:
        text('H1 主要預測來源：開盤前版（HGB，含前一晚美股）。下方 C0 基準只作備援對照，不作為 H1 主要顯示。')
        table(_preopen_rows(preopen_h1))
    summary=[('分析資料日',_day(payload.get('latest_data_date'))),(base+'本次結果','已產生估計' if estimated else '無法估計')]
    signal_close=pred.get('signal_close')
    if signal_close is None and report_view and report_view['dates'][-1]==payload.get('latest_data_date'):
        signal_close=report_view['close'][-1]
    summary.extend([('訊號日調整後收盤價',value(signal_close)+(' 元' if _report_float(signal_close) is not None else '')),
        (base+'上漲機率',value(pred.get('prob_up'),percent=True)),
        (base+'預估報酬',value(pred.get('expected_return'),percent=True,signed=True))])
    if not estimated:
        reasons=str(payload.get('no_estimate_reason') or '').split(',')
        summary.append(('原因','；'.join(dict.fromkeys(_REASON_ZH.get(reason,'資料或模型條件未滿足，詳見隨附資料檔') for reason in reasons))))
    table(summary)
    if preopen_used:
        text('開盤前版必要揭露','body')
        table([(str(name),str(detail)) for name,detail in preopen_h1.get('disclosure_rows') or []])
    text('本報告沿用現行模型；歷史時點資料、公司行動調整與歷史普通股資格尚未完整驗證，模型效果及交易效益未驗證。','small')
    # 摘要（含可交易名次）固定在第一頁，股價技術從第二頁開始。
    story.append(PageBreak())
    section('二','股價技術')
    if report_view and report_view.get('dates'):
        story.append(_report_price_chart(report_view,regular))
        text(f"資料範圍：{_day(report_view['dates'][0])}～{_day(report_view['dates'][-1])}，共 {len(report_view['dates'])} 個交易日。價格為已讀取的調整後收盤價，成交量為原始成交股數。",'small')
        missing=sum(item is None for item in report_view['close'])
        if missing:text(f"其中有效價格 {len(report_view['close'])-missing} 日、缺少 {missing} 日；圖表於缺日斷線，技術指標依各自所需資料計算。",'small')
        averages=report_view['moving_averages']
        table([('最近一日漲跌',value(report_view['daily_change'],percent=True,signed=True)),
            ('5／20／60日均線','／'.join(value(averages[period][-1]) for period in (5,20,60))+' 元'),
            ('近5／20／60日報酬','／'.join(value(report_view['returns'][period],percent=True,signed=True) for period in (5,20,60))),
            ('近20日每日報酬波動',value(report_view['volatility_20'],percent=True)),
            ('最近一日成交量',value(report_view['volume'][-1],digits=0)+' 股')])
        text('均線、歷史報酬及波動僅描述已發生的價格變化，不是新增模型訊號、未來價格區間或買賣建議。','small')
    else:
        table([(name,unavailable) for name in ('最近一日漲跌','5／20／60日均線','近5／20／60日報酬','近20日每日報酬波動','最近一日成交量')])
        text('本次沒有可呈現的有效價格序列，技術圖無法繪製；各指標保留並標示無法計算。')
    section('三','基本面估值')
    table([('公司／市場',f"{stock_name or '未提供'}／{_market_zh(stock.get('market'))}"),
        ('產業',stock.get('source_industry') or '未提供'),
        ('財務與估值','已讀取的當期資料與歷史比較見下表；未提供項目仍保留缺值。'
            if (payload.get('report_comparison') or {}).get('metrics') else
            '本次資料未提供財報、每股盈餘、本益比、股價淨值比或合理價估計。')])
    text('公司名稱及產業屬股票識別資料，不能替代基本面分析；未提供的估值不以模型報酬或其他股票資料推算。','small')
    comparison=payload.get('report_comparison') or {}
    comparison_rows=report_comparison_rows(payload)
    if comparison_rows:
        text('歷史比較與中文解讀','subtitle')
        for row in comparison_rows:
            table([(key,row[key]) for key in ('項目','資料期間','本期值','比較','解讀','資料日期')])
    for note in comparison.get('notes') or []:text(str(note),'small')
    section('四','籌碼市場新聞')
    table([('市場價格與量能','歷史價格與成交量見第二章；不等同法人籌碼。' if report_view else '本次未提供可呈現的價格與量能序列。'),
        ('法人與籌碼','已讀取的法人、融資券或集保比較見第三章歷史比較表；資料日期與缺值逐項保留。'
            if any(item.get('source_route') in {'finmind_institutional','finmind_margin','tdcc_share_distribution_raw'}
                   for item in comparison.get('metrics') or []) else '本次未提供法人買賣超、持股或其他籌碼資料。'),
        ('新聞與事件','本次未提供經來源確認的新聞及事件資料，不產生推測性新聞解讀。')])
    section('五','比較樣本與模型表現')
    table([('比較樣本方向判斷率',value(metrics.get('direction_accuracy'),percent=True)),
        ('方向判斷樣本數（排除持平）',value(metrics.get('nonflat_direction_count'),digits=0)),
        ('比較樣本總筆數',value(metrics.get('observed_count'),digits=0)),
        ('報酬率平均絕對誤差',value(metrics.get('mae'),digits=6)),
        ('模型訓練股票數',value((payload.get('training_scope') or {}).get('stock_count'),digits=0)),
        ('本次推論股票數',value((payload.get('coverage') or {}).get('eligible'),digits=0))])
    if metrics:
        text('平均絕對誤差以報酬率小數表示，不是價格誤差、百分點或預測區間。上述為比較樣本整體結果，不能稱為本股歷史準確率，也不能當成已驗證的樣本外績效。')
        if _report_float(metrics.get('direction_accuracy')) is not None and float(metrics['direction_accuracy'])<0.6:
            text('比較樣本方向判斷率低於60%；不可據此宣稱可靠預測。','small')
    else:text('本次沒有可用的模型比較資料；欄位保留並標示無法計算。')
    section('六','對應期限預測與說明')
    text(f'本報告目標：{scope_note}。'+('這是下一個交易日的估計，不是盤中價格預測。' if h==1 else '約三個月的交易日期限，不是固定日曆天數。'))
    if preopen_used:
        text('H1 主要數值取自開盤前版快照（見第一章）；下表的到期日、方法與校準欄位描述 C0 基準備援。','small')
    target=payload.get('target_session_date') or pred.get('target_session_date')
    table([('到期交易日',_day(target) if target else '尚未驗證，不推算日曆日期'),
        ('方向方法',_REPORT_METHOD_ZH.get(pred.get('selected_direction_kind'),'未提供')),
        ('報酬方法',_REPORT_METHOD_ZH.get(pred.get('selected_return_kind'),'未提供')),
        ('機率校準',_REPORT_CALIBRATION_ZH.get(pred.get('calibration_status'),'未提供')),
        ('前瞻校正',_REPORT_CALIBRATION_ZH.get(pred.get('forward_calibration_status'),'未提供'))])
    if estimated:
        if pred.get('selected_direction_kind')=='fit_up_rate_baseline':
            text('上漲機率採訓練樣本整體比率基準；同期限可能對多檔股票相同，不能作為個股相對排名。')
        if pred.get('selected_return_kind')=='zero_return_baseline':
            text('預估報酬0%是模型選用的零報酬基準，不代表未來股價不會變動。')
        text('目前模型未提供80%價格區間或上中下三種情境；本報告不自行製造區間、排名或情境機率。')
    else:text('本次方向機率與報酬估計保留為無法計算；其他已取得的資訊照常呈現，原因見第一章。')
    section('七','資料時間與限制')
    for line in _data_time_lines(payload):text(line,'small')
    statuses=payload.get('source_statuses') or {}
    if statuses:table([(_REPORT_ROUTE_ZH.get(route,'補充資料來源'),_REPORT_STATUS_ZH.get(str(status).upper(),'未提供或尚未確認')) for route,status in statuses.items()])
    else:text('本次未提供各資料來源的完成狀態。','small')
    text('資料來源為部分資料時仍保留其限制。完整模型識別、資料快照、程式差異及原始狀態紀錄保留於隨附資料檔；本報告不據此宣稱已驗證獲利能力。','small')
    def footer(canvas,document):
        canvas.saveState();canvas.setFont(regular,8);canvas.setFillColor(colors.HexColor('#64748B'))
        canvas.drawString(44,24,f"{stock['stock_id']}｜資料日 {_day(payload.get('latest_data_date'))}｜H{h}")
        canvas.drawRightString(551,24,f'第 {document.page} 頁');canvas.restoreState()
    document=SimpleDocTemplate(str(path),title=f"{stock['stock_id']} 個股分析報告 H{h}",author='DStock',
        pagesize=(595,842),leftMargin=44,rightMargin=44,topMargin=38,bottomMargin=42)
    document.build(story,onFirstPage=footer,onLaterPages=footer)


def _horizon(value):
    normalized=str(value).upper()
    if normalized not in {'H1','1','H63','63'}:
        raise ValueError('c0_horizon_must_be_H1_or_H63')
    return 63 if normalized in {'H63','63'} else 1


def _seal(payload):
    payload.pop('estimate_binding_sha256',None)
    payload['estimate_binding_sha256']=hashlib.sha256(_canonical(payload)).hexdigest()


def _write_json(path,payload):
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False,default=_json_value),encoding='utf-8',newline='\n')


def _report_paths(output_dir,code,horizon):
    output=Path(output_dir).resolve();output.mkdir(parents=True,exist_ok=True)
    stem=f"c0_{code}_h{horizon}_{_now_taipei().strftime('%Y%m%dT%H%M%S%f')}"
    return output/(stem+'.pdf'),output/(stem+'.json')


def _write_report(payload,pdf_path,json_path,validate=None,report_view=None):
    """PDF, then the sealed JSON (never an orphan PDF), then validation whose result is written back."""
    attach_report_comparison(payload)
    _seal(payload)
    _emit_phase(str(payload['stock']['stock_id']),'pdf');_pdf(pdf_path,payload,report_view)
    pdf_bytes=pdf_path.read_bytes()
    payload['pdf_sha256']=hashlib.sha256(pdf_bytes).hexdigest();payload['pdf_size']=len(pdf_bytes)
    # The HTML comparison preview consumes the same sealed metadata as PDF/JSON.
    preview_path=json_path.with_suffix('.html')
    preview_path.write_text(comparison_preview_html(payload),encoding='utf-8',newline='\n')
    payload['comparison_html']=str(preview_path)
    payload['verification']={'status':'pending' if validate is not None else 'not_applicable_no_estimate'}
    _seal(payload);_write_json(json_path,payload)
    if validate is not None:
        try:
            verified=validate(payload,pdf_bytes)
        except Exception as exc:
            # Record the failure in the already written JSON, then re-raise unchanged (never swallowed).
            payload['verification']={'status':'failed','error':type(exc).__name__+': '+str(exc)[:500],
                'integrity_error':_is_integrity(exc)}
            _seal(payload);_write_json(json_path,payload)
            raise
        payload['verification']={'status':'verified' if verified.get('verified') is True else 'unverified',
            'schema':verified.get('schema'),'reproduction_status':verified.get('reproduction_status'),
            'prediction_reproduced':verified.get('prediction_reproduced'),
            'version_labels':copy.deepcopy(verified.get('version_labels') or []),'verified_at':_now_taipei().isoformat()}
        _seal(payload);_write_json(json_path,payload)
    for line in preopen.display_lines(_preopen_block(payload)):print(line,flush=True)
    print(_data_time_summary(payload.get('data_time') or {}),flush=True)
    print('PDF：'+str(pdf_path),flush=True);print('JSON：'+str(json_path),flush=True)
    return payload


def _stock_exclusion(exc):
    """(reason, excluded records) when the panel excluded the requested stock itself; else (None, None)."""
    text=str(exc);prefix,_,detail=text.partition(':')
    if prefix not in _STOCK_EXCLUSION_PREFIXES:return None,None
    try:
        excluded=json.loads(detail) if detail else []
    except ValueError:
        excluded=[]
    reasons=sorted({str(item.get('reason')) for item in excluded if isinstance(item,dict) and item.get('reason')})
    return (','.join(reasons) or prefix),excluded


def _stock_last_price_date(context,code,cache_dir):
    """The stock's own last published price day (informational; integrity errors still raise)."""
    import stock_query_projection as query
    items=(context.get('catalog') or {}).get('finmind_price_adj') or ()
    if len(items)!=1:return None
    item=items[0]
    try:
        selected=query.query_published_rows(item['root'],item['artifact_descriptor'],'finmind_price_adj',
            stock_ids=[str(code)],source_identity=item.get('source_identity'),parent_identity=item.get('parent_identity'),
            allow_build=True,consumer_temp_root=cache_dir)
        days=[cd.iso_calendar_day(row.get('date')) for row in selected.iter_rows() if str(row.get('stock_id'))==str(code)]
    except query.ProjectionIntegrityError:
        raise
    except (OSError,LookupError,query.ProjectionError):
        # Availability only (unavailable/lock timeout/limit): the day is unknown; integrity errors above still raise.
        return None
    days=[day for day in days if day]
    return max(days) if days else None


def _stock_quality_fields(stock,source,prediction=None):
    prediction=prediction or {}
    assessment=prediction.get('data_quality_assessment')
    if not isinstance(assessment,dict):
        assessments=(source.get('evidence') or {}).get('stock_data_quality_assessments') or []
        assessment=next((item for item in assessments if isinstance(item,dict) and
            str(item.get('stock_id'))==str(stock['stock_id']) and
            str(item.get('market','')).upper()==str(stock.get('market','')).upper()),{})
    notes=[]
    for group in (prediction.get('stock_data_notes'),assessment.get('notes')):
        if isinstance(group,list):notes.extend(str(note) for note in group if note)
    return {'stock_data_notes':list(dict.fromkeys(notes)),'data_quality_assessment':_clean(assessment)}


def _base_fields(stock,horizon,source,loaded,data_time,labels,qa_override,pdf_path,json_path,asof=None,prediction=None):
    # The stock's own market day (a lagging market keeps its own asof; validate_c0_estimate checks the same day).
    asof=asof if asof is not None else source['asof']
    statuses=(source.get('source_binding') or {}).get('route_statuses')
    if statuses is None:statuses={route:item.get('status') for route,item in (source.get('required_route_evidence') or {}).items()}
    return {'report_schema_version':REPORT_SCHEMA,'profile':'C0','stock':stock,
        **_stock_quality_fields(stock,source,prediction),'source_statuses':statuses,'readiness':source.get('readiness'),
        'latest_data_date':asof,'analysis_asof':asof,'horizon_sessions':horizon,
        'target_definition':'signal_close_to_same_market_H_session_close',
        'historical_pit_verified':False,'historical_membership_verified':False,'corporate_actions_verified':False,
        'effectiveness_status':'unknown','trading_effect':'unverified','source_mode':'published',
        'generated_at':_now_taipei().isoformat(),'data_time':data_time,'version_labels':labels,
        'model_artifact_sha256':loaded['artifact_binding']['sha256'],'model_binding':loaded['artifact_binding'],
        'pointer_binding':loaded['pointer_binding'],'policy_binding':loaded['policy_binding'],
        'current_policy_binding':loaded['current_policy_binding'],'contract_adoption_verified':loaded['contract_adoption_verified'],
        'contract_adoption_status':loaded.get('contract_adoption_status'),'model_activation_epoch':loaded['model_activation_epoch'],
        'published_snapshot':source['descriptor'],'output_pdf':str(pdf_path),'output_json':str(json_path),'qa_override':qa_override}


def _no_estimate(output_dir,stock,horizon,source,loaded,*,reason,detail,stock_last,qa_override,labels,prediction=None,forward_binding=None,coverage=None,report_view=None,h1_source=None):
    """Plan 2.6 :115 / default #8: still a PDF/JSON stating why there is no estimate and the stock's last data day."""
    code=str(stock['stock_id'])
    stock_asof=_stock_market_asof(source,stock.get('market'))
    # H1: the pre-open row is resolved before any file is written (a required pre-open stops the run here).
    preopen_fields=_preopen_fields(stock,stock_asof,h1_source,(prediction or {}).get('target_session_date')) if horizon==1 else {}
    pdf_path,json_path=_report_paths(output_dir,code,horizon)
    data_time=_data_time(source,loaded);lag=_stock_market_lag_label(data_time,stock.get('market'),stock_asof)
    labels=_merge_labels(labels,[lag] if lag else [])
    payload=_base_fields(stock,horizon,source,loaded,_stock_data_time(data_time,stock.get('market'),stock_asof,lag),
        labels,qa_override,pdf_path,json_path,asof=stock_asof,prediction=prediction)
    metadata=(loaded.get('model') or {}).get('metadata') or {}
    selection=(metadata.get('selection_by_horizon') or {}).get(str(horizon),{})
    metrics=((loaded.get('model') or {}).get('metrics') or {}).get(str(horizon),{})
    pairs=(metadata.get('source_evidence') or {}).get('eligible_stock_market_pairs') or []
    payload.update({'report_class':NO_ESTIMATE_CLASS,'estimate_status':'no_estimate','no_estimate_reason':reason,
        'no_estimate_detail':_clean(detail),'stock_last_price_date':stock_last,'prediction':_clean(prediction) if prediction is not None else None,
        'prob_up':None,'expected_return':None,'target_session_date':(prediction or {}).get('target_session_date'),
        'comparison_metrics':selection.get('selected_metrics') or metrics.get('all') or {},
        'comparison_evaluation_role':metadata.get('evaluation_role'),'selection_evidence':selection,
        'training_scope':{'stock_count':len(pairs),'stock_market_pairs':pairs},
        'coverage':_clean(coverage) if coverage is not None else None,'forward_binding':_clean(forward_binding) if forward_binding is not None else None,
        **preopen_fields})
    reason_zh='；'.join(dict.fromkeys(_REASON_ZH.get(part,'資料或模型條件未滿足') for part in str(reason).split(',') if part)) or '資料或模型條件未滿足'
    print('C0無可用估計（照樣輸出報告）：'+code+' H'+str(horizon)+'：原因：'+reason_zh+'（'+str(reason)+'）；該股最後價格日 '+_day(stock_last),file=sys.stderr,flush=True)
    return _write_report(payload,pdf_path,json_path,report_view=report_view)


def generate_estimate(horizon,stock_query,*,output_dir,cache_dir=None,max_matches=1,model_path=None,forward_db=None,both_horizons=False,
                      pinned_head_sha256=None,h1_source=None):
    import stock_daily_linear as linear
    horizon=_horizon(horizon)
    horizons=[1,63] if both_horizons else [horizon]
    # H1 display source (auto|preopen|c0); H63 always keeps the C0 source.
    h1_source=preopen.resolve_source(h1_source) if 1 in horizons else None
    qa_override=model_path is not None or forward_db is not None
    loaded=contract.load_active_c0_model(model_path,consumer_id='single')
    # One pin for the whole run: this context (and its reader) is reused below; no second context load here.
    # pinned_head_sha256 (the caller's job-start pin) selects that immutable head while it still exists.
    context,pin_labels=_load_pinned_context(loaded,pinned_head_sha256)
    identity=context['current_identity']
    matches=_resolve(stock_query,identity,max_matches)
    if len(matches)!=1:raise ValueError('c0_analysis_requires_exactly_one_current_ordinary_stock')
    stock=matches[0];code=str(stock['stock_id']);_emit_phase(code,'data_check')
    _emit_phase(code,'data_prepare')
    try:
        inputs=contract.load_c0_published_panel([code],context_or_sources=context,consumer_id='single',cache_dir=cache_dir,runtime_binding=loaded)
    except contract.C0IntegrityError as exc:
        reason,excluded=_stock_exclusion(exc)
        if reason is None:raise
        source=context;assessments=getattr(exc,'data_quality_assessments',None)
        if isinstance(assessments,list):
            source={**context,'evidence':{**(context.get('evidence') or {}),'stock_data_quality_assessments':assessments}}
            last=next((item.get('last_price_date') for item in assessments if str(item.get('stock_id'))==code),None)
        else:last=_stock_last_price_date(context,code,cache_dir)
        payloads=[_no_estimate(output_dir,stock,value,source,loaded,reason=reason,detail=excluded,stock_last=last,
            qa_override=qa_override,labels=_merge_labels(context.get('version_labels')),
            coverage=getattr(exc,'coverage',None),h1_source=h1_source) for value in horizons]
        return payloads if both_horizons else payloads[0]
    # Same pinned read (torn-read guard, KEEP).
    if inputs['descriptor']['snapshot_fingerprint']!=context['descriptor']['snapshot_fingerprint']:
        raise contract.C0IntegrityError('c0_analysis_snapshot_changed')
    _with_labels(inputs,pin_labels)
    model=loaded['model'];_emit_phase(code,'model')
    # The stock's OWN market day (core asof_by_market): a stock in a lagging market is
    # built, predicted and reported with that market's day (labelled), never excluded
    # and never with another market's later day (anti-lookahead: build_panel rejects rows after it).
    stock_asof=_stock_market_asof(inputs,stock['market'])
    report_view=_build_report_view(inputs['panel'],code,stock_asof,inputs['market_sessions'])
    prepared=linear.build_panel(inputs['panel'],inputs['market_sessions'],asof=stock_asof,horizons=horizons,evidence=inputs['evidence'],policy_binding=loaded['policy_binding'])
    predictions=linear.predict_baseline(model,prepared)
    forwarded=contract.process_c0_forward(predictions,inputs,loaded,db_path=forward_db)
    runtime_binding=_runtime_binding(inputs,loaded)
    data_time=_data_time(inputs,loaded)
    lag=_stock_market_lag_label(data_time,stock['market'],stock_asof)
    data_time=_stock_data_time(data_time,stock['market'],stock_asof,lag)
    labels=_merge_labels(inputs.get('version_labels'),[lag] if lag else [],(forwarded.get('forward_binding') or {}).get('version_labels'))
    predictions=__import__('pandas').DataFrame(forwarded['predictions'])
    payloads=[]
    for horizon in horizons:
        matches=predictions.loc[(predictions.stock_id==code)&(predictions.horizon==horizon)]
        if len(matches)!=1:raise ValueError('c0_prediction_identity_or_horizon_missing')
        result=matches.iloc[0].to_dict()
        if str(result.get('market'))!=str(stock['market']):raise ValueError('c0_prediction_market_mismatch')
        stock_last=cd.iso_calendar_day(result.get('stock_last_price_date'))
        if result.get('prediction_status')!='predicted':
            payloads.append(_no_estimate(output_dir,stock,horizon,inputs,loaded,reason=str(result.get('reason') or 'prediction_unavailable'),
                detail={'prediction_status':result.get('prediction_status')},stock_last=stock_last,qa_override=qa_override,
                labels=labels,prediction=result,forward_binding=forwarded['forward_binding'],coverage=inputs['coverage'],report_view=report_view,
                h1_source=h1_source))
            continue
        for name in ('prob_up','expected_return'):
            if not math.isfinite(float(result[name])):raise ValueError('c0_prediction_nonfinite')
        if not 0<=float(result['prob_up'])<=1:raise ValueError('c0_probability_out_of_range')
        # H1: resolve the pre-open row before any file is written (a required pre-open stops the run here).
        preopen_fields=_preopen_fields(stock,stock_asof,h1_source,result.get('target_session_date')) if horizon==1 else {}
        pdf_path,json_path=_report_paths(output_dir,code,horizon)
        metrics=(model.get('metrics') or {}).get(str(horizon),{});selection=(model.get('metadata',{}).get('selection_by_horizon') or {}).get(str(horizon),{})
        selected_metrics=selection.get('selected_metrics') or metrics.get('all') or {}
        payload=_base_fields(stock,horizon,inputs,loaded,data_time,labels,qa_override,pdf_path,json_path,asof=stock_asof,prediction=result)
        payload.update({'report_class':'daily_estimate_effect_unknown','estimate_status':'estimated',
            'stock_last_price_date':stock_last,'in_sample_lookahead':bool(result.get('in_sample_lookahead')),
            'target_session_date':result.get('target_session_date'),
            'future_calendar_status':result['future_calendar_status'],'prediction':result,'prob_up':float(result['prob_up']),
            'expected_return':float(result['expected_return']),'calibration_status':result['calibration_status'],
            'comparison_metrics':selected_metrics,
            'comparison_evaluation_role':model['metadata'].get('evaluation_role'),'selection_evidence':selection,
            'model_feature_schema':loaded['artifact_binding']['feature_schema'],
            'model_feature_order':loaded['artifact_binding']['feature_order'],'requirements_version':loaded['current_policy_binding']['requirements_version'],
            'training_scope':{'stock_count':len(model['metadata']['source_evidence']['eligible_stock_market_pairs']),'stock_market_pairs':model['metadata']['source_evidence']['eligible_stock_market_pairs']},
            'source_statuses':inputs['source_binding']['route_statuses'],
            'data_binding':inputs['source_binding'],
            'original_model_feature_contract':loaded['original_model_feature_contract'],
            'contract_adoption_ref':loaded['contract_adoption_ref'],
            'effective_C0_semantics_projection_sha256':loaded['effective_C0_semantics_projection_sha256'],
            'original_effective_C0_semantics_projection_sha256':loaded['original_effective_C0_semantics_projection_sha256'],
            'current_identity':inputs['current_identity'],
            'readiness':inputs['readiness'],
            'coverage':inputs['coverage'],'forward_binding':forwarded['forward_binding'],
            'unmet_conditions':['historical_PIT_unverified','historical_membership_unverified','corporate_actions_unverified','effectiveness_unknown'],
            **preopen_fields})
        validate=lambda value,pdf_bytes,horizon=horizon:contract.validate_c0_estimate(value,pdf_bytes,stock_id=code,horizon=horizon,
            expected_snapshot_fingerprint=inputs['descriptor']['snapshot_fingerprint'],
            allow_qa_override=qa_override,expected_runtime_binding=runtime_binding)
        payloads.append(_write_report(payload,pdf_path,json_path,validate,report_view=report_view))
    return payloads if both_horizons else payloads[0]


def _is_integrity(exc):
    """C0IntegrityError (shared or reader copy) or the query layer's ProjectionIntegrityError."""
    return isinstance(exc,contract.C0IntegrityError) or type(exc).__name__ in {'C0IntegrityError','ProjectionIntegrityError'}


def _unavailable(exc):
    """Data/environment unavailability (not an integrity or content verdict)."""
    projection=sys.modules.get('published_single_stock_projection')
    pin_unavailable=getattr(projection,'PublishedPinUnavailableError',None)
    query=sys.modules.get('stock_query_projection')
    projection_error=getattr(query,'ProjectionError',None)
    if _is_integrity(exc):return False
    if projection_error is not None and isinstance(exc,projection_error):return True
    if pin_unavailable is not None and isinstance(exc,pin_unavailable):return True
    return isinstance(exc,(OSError,ImportError,KeyError,TypeError))


def main(horizon,argv=None):
    parser=argparse.ArgumentParser(description='單股收盤至市場交易日收盤估計；使用獨立已採用模型，歷史PIT與效果限制保留',allow_abbrev=False)
    parser.add_argument('--stock');parser.add_argument('--resolve-stock',metavar='QUERY')
    parser.add_argument('--resolve-only',action='store_true');parser.add_argument('--output-json',type=Path)
    parser.add_argument('--both-horizons',action='store_true',help='同一次已發布快照產生H1與H63收盤報告')
    parser.add_argument('--max-matches',type=int,default=1);parser.add_argument('--output-dir',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--cache-dir',type=Path);parser.add_argument('--no-progress',action='store_true');parser.add_argument('--c0-model',type=Path);parser.add_argument('--forward-db',type=Path)
    parser.add_argument('--pinned-head-sha256',metavar='SHA256',
        help='使用呼叫端開始時的已發布 head（_manifests/<sha>.json）；C0缺失時改用目前head並記標籤，候選模式缺失即停止')
    parser.add_argument('--candidate-model',type=Path,
        help='明確使用候選模型產生研究報告；不改單股採用指標、共用模型或前瞻帳本')
    parser.add_argument('--horizons',help='僅候選模式：市場交易日期限，以逗號分隔；預設沿用入口期限')
    parser.add_argument('--h1-source',choices=preopen.SOURCES,
        help='H1 顯示來源：auto（預設；開盤前版快照新鮮就用，否則回退 C0 基準並標示原因）、'
             'preopen（必須用開盤前版，不可用即停止）、c0（強制舊行為）；未指定時讀環境變數 '+preopen.SOURCE_ENV)
    args=parser.parse_args(argv)
    if args.candidate_model is not None:
        if args.c0_model is not None or args.forward_db is not None or args.resolve_only or args.resolve_stock is not None or args.output_json is not None:
            parser.error('候選模式不可混用正式模型、前瞻帳本或舊C0解析模式')
        if args.h1_source is not None:parser.error('候選研究模式不使用 --h1-source（開盤前版只套用於正式 H1 報告）')
        if not args.stock or not args.stock.strip():parser.error('候選模式必須指定 --stock')
        try:
            import stock_daily_candidate_runtime as candidate
            horizons=candidate._horizons(args.horizons) if args.horizons else ((1,63) if args.both_horizons else (_horizon(horizon),))
            output=args.output_dir if args.output_dir!=DEFAULT_OUTPUT else candidate._new_run(candidate.CANDIDATE_ROOT/'reports')/'reports'
            candidate.generate_candidate_reports(args.stock,model_path=args.candidate_model,
                output_dir=output,cache_dir=args.cache_dir,horizons=horizons,pinned_head_sha256=args.pinned_head_sha256)
            return 0
        except (OSError,ValueError,KeyError,TypeError,ImportError,RuntimeError) as exc:
            print('候選研究受阻：'+type(exc).__name__+'：'+str(exc),file=sys.stderr,flush=True)
            return 2
    if args.horizons is not None:parser.error('--horizons 必須搭配 --candidate-model')
    if args.max_matches<0:parser.error('--max-matches 不可為負數')
    if args.resolve_stock is not None and args.stock is not None:parser.error('只能指定一個股票查詢（--stock 與 --resolve-stock 擇一）')
    query=args.resolve_stock if args.resolve_stock is not None else args.stock
    if not query or not query.strip():parser.error('必須指定 --stock 或 --resolve-stock')
    if args.output_json is not None and not (args.resolve_only or args.resolve_stock is not None):parser.error('--output-json 必須搭配股票解析模式（--resolve-stock 或 --resolve-only）')
    try:
        horizon=_horizon(horizon)
        if args.resolve_only or args.resolve_stock is not None:
            job=selected.analysis_binding() if os.environ.get(selected.BINDING_ENV) else None
            pin=args.pinned_head_sha256 or ((job or {}).get('head_sha256'))
            context=selected.load_selected_context(pin)
            identity=context['current_identity']
            matches=_resolve(query,identity,args.max_matches)
            if not matches:raise ValueError('current_ordinary_stock_not_found')
            data_time=context.get('data_time') or contract.build_data_time(context)
            for index,stock in enumerate(matches,1):print(f"{index}. {stock['stock_id']} {stock.get('stock_name','')}｜{stock['match_type']}｜score={stock['match_score']}")
            print(_data_time_summary(data_time),flush=True)
            if args.output_json is not None:
                document={'schema':'single-current-selected-stock-resolution-v1','query':query,'resolved_stocks':matches,
                    'current_identity':identity,'published_snapshot':context['descriptor'],'asof':context['asof'],
                    'current_policy_binding':context.get('current_policy_binding'),
                    'generated_at':_now_taipei().isoformat(),'data_time':data_time,
                    'version_labels':_merge_labels(context.get('version_labels'))}
                args.output_json.parent.mkdir(parents=True,exist_ok=True)
                _write_json(args.output_json,document)
            return 0
        # Only runs containing H1 validate its CLI/environment display source (invalid value: rc=2).
        horizons=(1,63) if args.both_horizons else (horizon,)
        h1_source=preopen.resolve_source(args.h1_source) if 1 in horizons else None
        if args.c0_model is None:
            binding=selected.analysis_binding()
            if binding is not None:
                if args.pinned_head_sha256 and not os.environ.get(selected.BINDING_ENV):
                    binding=selected.rebind_job_head(binding,args.pinned_head_sha256)
                if args.forward_db is not None:
                    parser.error('--forward-db 只可搭配明確的 --c0-model QA，正式單股不寫共用前瞻帳本')
                _emit_phase(query,'data_check')
                # Single-stock wrapper: the shared generator is unchanged; only the formal H1 payload gains the
                # sealed pre-open block (stock_candidate_report.write_candidate_report, before rendering).
                with (preopen.selected_h1_overlay(h1_source) if 1 in horizons else nullcontext()):
                    selected.generate_selected_reports(query,output_dir=args.output_dir,cache_dir=args.cache_dir,
                        horizons=horizons,
                        pinned_head_sha256=args.pinned_head_sha256,activation_binding=binding)
                return 0
        generate_estimate(horizon,query,output_dir=args.output_dir,cache_dir=args.cache_dir,max_matches=args.max_matches,model_path=args.c0_model,forward_db=args.forward_db,both_horizons=args.both_horizons,
            pinned_head_sha256=args.pinned_head_sha256,h1_source=h1_source)
        return 0
    except (OSError,ValueError,KeyError,TypeError,ImportError,RuntimeError) as exc:
        query_module=sys.modules.get('stock_query_projection')
        if isinstance(exc,RuntimeError) and not (query_module is not None and isinstance(exc,query_module.ProjectionError)):
            raise  # unrelated RuntimeError: unchanged (traceback, rc=1)
        if _unavailable(exc):
            # Not an integrity/content verdict: the data or environment could not be read (rc=3).
            print('C0分析無法執行（資料讀取或執行環境）：'+type(exc).__name__+': '+str(exc),file=sys.stderr,flush=True)
            return 3
        # Integrity (C0IntegrityError) or content errors only (rc=2).
        print('C0分析受阻：'+type(exc).__name__+': '+str(exc),file=sys.stderr,flush=True)
        return 2
