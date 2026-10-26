# finmind_price_adj / finmind_per: data in head cccbca1f vs head efbd4844 (read-only comparison, 2026-10-06)

Everything here was read-only on D:. The work used parquet copies in `data/parquet/`, scripts in `data/scripts/` and outputs in `data/out/` and `data/xs/`.
Program code ran under fix/dguard with no violations. The head sha256 was the same before and after every probe.
The quiet temp root `C:\Users\USER\claude_quiet_hpdata` was moved to `three_month_impl/_trash/claude_quiet_hpdata_20261006`. Peak working set was about 1.1 GB.

## 0. Bottom line

| question | answer |
|---|---|
| Does the new price object contain 2026-10-05 for all TWSE/TPEx stocks? | **No.** It has 540 securities on 10-05. The previous sessions had 2,818-2,823. For four-digit codes, using the 10-02 board: TWSE 300 of 1,094, TPEx 140 of 888, Emerging 31 of 361. 2330, 2317 and 0050 have no 10-05 row; 2454 has one. PER has **zero** 10-05 rows (previous sessions 1,968-1,973). |
| Where does the 10-05 data come from? | Only from the same-run "price vintage repair": a per-stock full-series refetch of 700 stocks, payload written 10-05 22:31. The whole-market per-date payloads (`05-finmind_price_adj.json` written 05:33 and `15-finmind_per.json` written 05:24 on 10-05) were produced **before the 10-05 session**. Their 10-05 unit returned "success; response contains no records". The resume at 22:05 republished those morning payloads. |
| What is `vintage_history_conflict`? | It is a whole-stock refusal in the store merge (`stock_shared_store.py` around lines 4392-4420). A stock is refused when any overlapping row differs in OHLC (exact float equality) and the fresh series does not cover the stored [min,max] range with newer stamps. All of that stock's candidate rows are dropped, including new dates. Two steps on 10-05 caused it: the step-1 whole-market refetch flagged 700 stocks, and the step-2 repair still refused 133 of them. |
| How many historical rows differ (old a5fe7c55 vs new fbb9fb50)? | 367,797 common rows (12.0 %) in 658 securities, on 1,211 of 1,214 dates. Only OHLC and spread changed; volume, money and turnover are unchanged. 347,200 of those rows differ by < 0.1 % (median relative 4e-5, provider recomputation noise). 20,162 rows differ by >= 0.1 %. For TWSE/TPEx four-digit stocks that is 12,534 rows in 133 stocks, explained by corporate-action re-adjustment and no-trade placeholder rows (section 3). There are 744 added rows (540 on 10-05) and no removed rows. |
| Is the new object internally consistent (one adjustment basis per stock)? | **Much more than the old one.** I checked the adj/raw factor against official closes over the full history for four-digit TWSE/TPEx stocks. Old: 43 unexplained basis breaks (29 of them in Sep 2026) and 4 missed ex-date adjustments since 2025-06. New: 15 breaks, all of them the listing-day first row (present in both objects), and 1 missed ex-date (7631, one of the refused 133). The efbd4844 object used for all earlier verification, including the 10-02 build, had mixed bases in the September window. |
| PER old vs new | **Identical values**: same 2,229,362 rows in the same order, 0 differing fields. Only `_shared_collected_at` was restamped (1,852,593 rows), so the file size is identical. It is PARTIAL only because 10-05 had no records. |
| Do the three-month features and P change materially at the latest buildable date (D=10-02)? | **Not for the universe as a whole.** Eligibility, E/P, B/M and revenue are unchanged. 1,081 of 1,092 eligible stocks move P by less than 0.001. 4 stocks move by 0.04-0.07 (3356, 6550, 2542, 1442) and 3 labels change materially. Band tables move by at most 1.5e-4 sigma-units. 2330: P 0.58055 to 0.58019, band unchanged. The changes are corrections: the old data carried fake returns for these stocks. |
| Deploy-copy xs on this head | Route checks: price PARTIAL, PER PARTIAL, **official_market_daily stale** (max 10-02 < D=10-05). Revenue and deadline pass. With the PARTIAL status bypassed in the scratch probe: `xs_route_stale:official_market_daily`. With that also bypassed: `xs_board_missing_at_d`. If the board had 10-05 rows, the board gate would still fail (TWSE 300 < 773, ratio 0.27 < 0.79). **No 10-05 cross-section is possible on this head, whatever the PARTIAL rule.** |

## 1. Objects and lineage

| head (local time) | price object | price status / rows / max | PER object |
|---|---|---|---|
| efbd4844 (10-05 07:52) | a5fe7c55 | SUCCESS / 3,072,673 / 10-02 | bb5504bb SUCCESS |
| 0bf8d15c (10-05 22:25) | e35ef242 (step 1, candidate 78eac9cc) | PARTIAL, warning `vintage_history_conflict`, coverage 1214/1213, missing 10-05 / 3,072,818 / 10-02 | bb5504bb |
| 2420b139 (10-05 22:37) | **fbb9fb50** (step 2 = vintage repair, candidate 76707823) | PARTIAL, coverage 700/700, warnings `prior_partial_unresolved`, `vintage_history_conflict` / 3,073,417 / 10-05 | bb5504bb |
| ddfd4caa (10-05 23:20) | fbb9fb50 | " | **d0ed56de** PARTIAL 999/1000, missing 10-05 |
| cccbca1f (10-06 01:23, current) | fbb9fb50 | " | d0ed56de |

All other inputs of the three-month xs are the same objects in efbd4844 and cccbca1f (official_market_daily 6265b799, month_revenue 7ec7b0e0, deadline 500ccb7c); the probe asserts this.

**Step 1 (e35ef242).** A whole-market per-date refetch of 2021-10-05..2026-10-05 (1,214 units, 10-05 empty), with receipts observed 2026-10-04 19:13 +08:00. 791 stocks had at least one OHLC mismatch with the stored rows:
- 91 stocks (all listed after 2021-10-04) were replaced wholesale. That changed 29,594 rows and added 145 rows.
- 700 stocks were refused:
  - 567 because the stored series starts 2021-10-04 while the fresh series starts 2021-10-05 (the requested window moved one day).
  - 133 because the stored first row (the listing day; open = 0 in 102 of them) is not returned by any fresh fetch.

Of the 700 refusals, 408 had max relative close differences below 0.1 %:
- 15 had no close difference at all (the mismatch was in another OHLC field).
- 12 differed by < 1e-6.
- 244 differed by 1e-6..1e-4.
- 137 differed by 1e-4..1e-3.

The exact-equality rule turns provider rounding noise into whole-stock refusals.

**Step 2 (fbb9fb50).** `price_vintage_repair.py` fetched the per-stock range 2021-10-04..2026-10-05 for the 700 stocks, all successfully. The merge adopted 567 stocks wholesale: all their rows in the new object equal the repair payload, 338,203 rows changed versus e35 and 599 rows were added, including the 540 rows of 10-05. It refused 133 again, because their per-stock series starts one session after the stored first row (`bounds[1][0] <= bounds[0][0]` fails; checked for all 133).
- These 133 keep the 10-03-run vintage. 78,658 rows still carry `_shared_collected_at` 2026-10-03, and their 10-05 rows were dropped: 673 stocks had 10-05 in the repair payload, but only 540 in the object.
- They are listed in `D:\TWStockData\updater_runtime\control\price_vintage_repair.json` (133 stocks, detected 14:37:56Z). Of them 110 are four-digit codes: 36 TWSE, 42 TPEx and 55 Emerging by market, ETFs and ETNs included.
- A later repair will be refused for the same reason unless the rule or the stored first row changes. While a stock is refused, it gets **no new dates at all**.

## 2. 2026-10-05 coverage (task item 1)

| | rows on 10-05 | four-digit TWSE | four-digit TPEx | four-digit Emerging |
|---|---|---|---|---|
| new price object | 540 | 300 | 140 | 31 |
| repair payload (76707823) | 673 | 322 | 173 | 86 |
| 10-02 (same object) | 2,819 | 1,094 | 888 | 361 |
| previous sessions 09-22..10-02 | 2,818-2,823 | 1,092-1,094 | 887-888 | 359-361 |

- The 10-05 rows are internally sound: for all 540, close - spread equals the 10-02 close. 13 of them have zero volume.
- PER has no 10-05 rows. The xs needs PER only up to the previous session (10-02), which is present.
- The price entry declares **no dated gap**. `gaps`, `prior_gaps` and the `partial_recovery` lists are all empty, so `xio._gap_dates(price)` returns [] and nothing undated. 10-05 appears only in `metadata.prior_coverage.missing_dates`, which describes the step-1 attempt, and `actual_max_date` says 10-05. A consumer that relaxed the status rule could not see from the metadata that 10-05 is about 19 % complete.
- By contrast, PER declares the 10-05 gap.

## 3. Historical differences, old a5fe7c55 vs new fbb9fb50 (task item 2)

Sources: `out/diff_price_old_a5fe7c55__price_new_fbb9fb50.json`, `out/row_breakdown.json` and `out/classify_*.csv`.

**Totals.**
- 3,072,673 common keys, 0 rows only in old, 744 rows only in new (540 on 10-05 and 204 historical).
- 367,797 rows differ in some field. By field: open 365,397; max/high 367,166; min/low 367,146; close 367,145; spread 313,573; Trading_Volume, Trading_money, Trading_turnover and volume 0.
- Differing rows by year: 2021 25,071; 2022 98,247; 2023 88,112; 2024 76,839; 2025 56,942; 2026 22,586.

**By security type and magnitude** (relative close difference):

| type | rows < 0.1 % (stocks) | rows >= 0.1 % (stocks) | max |
|---|---|---|---|
| four-digit TWSE | 226,712 (281) | 6,161 (38) | 0.92 |
| four-digit TPEx | 62,732 (94) | 6,373 (95) | 1.32 |
| four-digit Emerging | 23 (12) | 174 (34) | 0.14 |
| four-digit, no current board | 5,975 (10) | 93 (1) | 0.0015 |
| ETF/ETN (0xxxx) | 32,348 (47) | 3,450 (40) | 0.29 |
| preferred/TDR/other | 17,548 (24) | 1,518 (15) | 0.08 |
| warrants (6-digit 7xxxxx) | 1,862 (5) | 2,393 (38) | 23.1 |

**Causes** for TWSE/TPEx four-digit rows that moved >= 0.1 %. There are 12,534 such rows, split by window: older than 252 sessions 9,482; 64-252 sessions 1,878; last 63 sessions 1,174.

1. **Whole-history rescale for a new corporate action (about 10 stocks, about 11,300 rows).** The new object applies a constant factor to the full history before the event; the old object never did.
   - Capital reductions with a halt and resumption: 6550 x1.924 (resumed 09-29), 4806 x1.43/1.53 (resumed 10-02), 1441 x1.162 (resumed 09-29).
   - Ex-dividend or ex-rights dates 09-29..10-02: 6171 x0.927 (ex-div 10-02, 27.35 to 25.35), 9927 x0.929 (10-01), 6834 x0.966 (ex-rights 10-02), 2109 x0.967 (09-29), 1784, 6523 and 8440 (09-30).
2. **Window re-adjustment on traded days in the last 63 sessions (707 rows, 26 stocks).**
   - Old rows 09-04..09-18 sat at factor 1.0 while the rows before and after were adjusted for the late-September ex-date: 1235, 1442, 1595, 1808, 2542, 3260, 4109, 5258, 8042 (factor 0.91-0.99).
   - Old rows 09-04..09-09 of capital-reduction stocks were unscaled while the rows before 09-04 were scaled: 2321 x1.828, 8277 x2.319, 8059 x1.666, 3710 x1.622, 3356 x1.145, 3591 x1.066.
   - The old object therefore contained fake one-day returns at the window edges, for example -45 % for 2321 on 09-04 and +6.3 % / -5.9 % for 1235 on 09-04 and 09-21.
3. **No-trade-day placeholder rows (467 rows, 110 stocks, last 63 sessions).** These are days with no official close (volume 0 or odd-lot only).
   - The old per-date snapshot often filled these with a **later** bar, which is look-ahead inside the series. Example: 4169 on 07-23 held 223.0, which is the 07-27 close. 2924 on 07-14 held the 07-17 bar.
   - The per-stock series carries the previous close (4169 07-23 = 275.0).
   - Over the last 63 sessions, TWSE/TPEx rows whose placeholder equals the next traded close: old 66 rows (40 stocks), new 34 rows (30 stocks). The rest remain in the refused and not-repaired stocks; see `out/notrade_fill.json`.
4. **Rounding-level recomputation (below 0.1 %).** 347,200 rows in about 450 securities across all years. Example: 6597, 899 rows at a ratio of 0.999993.

**Adjudication.**
- On every TWSE/TPEx four-digit row since 2025-06 that differs by > 0.1 %, the OHLC shape ratios (open, high and low relative to close) match official_market_daily in **both** objects. The differences are pure rescaling, not corrupted bars.
- Of the 3,330 such rows that have an official bar, 2,045 old rows equal the raw close (unadjusted), and no new row does.

**Added historical rows (204).** Most are ETNs and warrants. 24 rows are four-digit stocks. Some are real traded days missing from the old object (3064 08-26, 6228 09-16, 6236 09-18, 7949 09-22..09-29 on Emerging); the others are zero-volume placeholders.

## 4. Internal consistency: one adjustment basis per stock (task item 4)

Method: compute f = adj_close / official raw close per (stock, date). A jump in f counts as explained when an ex-event from finmind_dividend_result falls in (prev_date, date], or when the stock had at least 3 missing sessions (halt and resumption, i.e. a capital change). Scripts: `factor_consistency2*.py` and `exdate_check.py`.

| check | old a5fe7c55 | new fbb9fb50 |
|---|---|---|
| unexplained basis breaks, four-digit TWSE/TPEx, 2021-10..2026-10 | 43 (29 in Sep 2026) | **15, all on the listing-day first row** (3716, 3717, 6854, 6862, 6887, 6907, 6924, 6949 [-95 %], 6957, 6962, 6965, 6969, 6988, 7717, 7749); the same in both |
| unexplained breaks, all securities, full history | 207 | 163 |
| ex-dates since 2025-06 (4,433 events): adjustment missing | 12 (four-digit: 3260 09-21, 2109 09-29, 7631 09-29, 8440 09-30) | 4 (three bond ETFs and 7631) |
| factor at each stock's latest row = 1 | yes for all | yes for all |

**By construction:**
- The 567 repaired stocks are one per-stock series fetched on 10-05 around 22:30, so they are consistent.
- The 133 refused stocks keep the old vintage. 7631, for example, misses its 0.58 % 09-29 ex-dividend adjustment.
- The remaining about 2,270 securities come from per-date whole-market snapshots. No unexplained four-digit breaks remain in them, but the no-trade placeholders listed in section 3 remain.

**Listing-day rows.** 299 four-digit stocks have a first row with open = 0 (the listing-day artefact). These rows create the 15 remaining basis breaks, and they are also why the 133 stocks can never be repaired.

## 5. PER (finmind_per)

d0ed56de and bb5504bb have identical keys, values and row order: PBR, PER, dividend_yield, pbr and per all have 0 differences.
- 1,852,593 rows (2022-08-19..2026-10-02) were refetched and restamped `2026-10-04T20:51:59+00:00`.
- 376,769 rows (2021-10-04..2022-08-18) kept their 10-03 stamp, under the new "last 3 years" window with `preserve_existing`.
- The 10-05 unit returned "success; response contains no records". The payload was written 05:24 on 10-05, before the session.

## 6. Three-month features, P and bands (task item 3 and the 10-02 comparison)

Tree: the deploy copy `D:\TWStockData\program_data\dstock_single\deploy_staging\20261005T135439Z\programs` (io e2341538, recipe 1974f158).

- `d1002_old`: D=2026-10-02 with the price and PER references taken from the efbd4844 manifest (a5fe7c55 and bb5504bb, SUCCESS). It **reproduces the 10-02 build exactly**: rows sha fde56da8 and band tables sha 514702e7, the same as og_gap `xs_new`.
- `d1002_new`: D=2026-10-02 on the current head objects (fbb9fb50 and d0ed56de), with only the PARTIAL status bypassed. It builds and passes every gate: eligible 1,092, all component coverage the same as before, revenue current share 1.0, board rows TWSE 1,086 / TPEx 888. Rows sha 2335ac5f, band sha 9e46e12d.

P is computed as sigmoid(b * score_centred), with b = 0.94862 inferred from the 2330 report (P 0.5805496 at score_centred 0.3426329). A retrain would move b slightly.

| quantity (1,092 eligible) | change old to new |
|---|---|
| eligible or in-scope membership | 0 changes |
| f_ep, f_bm, f_rev_yoy, f_rev_high12, f_money60 | 0 changes |
| f_vol60 / sigma63 | 67 stocks changed; p99 abs 0.0047; max 1.12 (6550: 1.61 to 0.47) |
| f_hi52 | 66 changed; p99 5e-5; max 0.21 (6550) |
| f_max5 | 13 changed; max 0.15 (6550) |
| f_ivol60 | all stocks changed by spillover through the market return (median 1.5e-4, max 0.002 for unaffected stocks); material for about 10 |
| P | 86 unchanged; 995 below 0.001; 5 in 0.001-0.005; 2 in 0.005-0.01; 4 at 0.01 or more: 3356 0.510 to 0.579, 6550 0.392 to 0.457, 2542 0.477 to 0.534, 1442 0.542 to 0.583 |
| relative-strength label (0.45/0.55) | 8 flips. 3 are material: 1442 and 3356 neutral to strong, 6550 weak to neutral. 5 are boundary flips with abs(dP) < 0.0005: 1314, 2034, 3044, 4976, 5234 |
| band tables (Mondrian vol5) | max abs(dQ) 1.5e-4 sigma-units; edges equal to 1e-8; pool 1,099,188 in both |
| per-stock band (via own sigma63) | 5 bucket changes (6550, 3356, 1442, 1218, 2542); 6550 q10/q90 -61 %/+116 % to -21 %/+36 %; median abs(dq90) 3e-6 |
| 2330 | P 0.58055 to 0.58019; sigma63 0.1712 unchanged; band q10 -17.29 %, q90 +28.81 % unchanged |
| 2317 | P 0.59481 to 0.59445; band unchanged to 1e-4 |

**Reading.** Wherever the move is material, the old data was wrong: a mixed basis, an unapplied capital reduction or ex-dividend, or a look-ahead placeholder. The new values are the corrected ones. The weekend training panel would also change slightly, because of the full-history rescales of about 10 stocks and the rounding-level noise everywhere.

## 7. Deploy-copy xs route checks and the D=2026-10-05 probe

`xs_probe.py checks` was run without any bypass. The head's expected_asof is 2026-10-05; data_date is 10-05.

| route | check_route at D=10-05 |
|---|---|
| finmind_price_adj | `xs_route_status:finmind_price_adj:PARTIAL` |
| finmind_per | `xs_route_status:finmind_per:PARTIAL` (declared gap 2026-10-05) |
| finmind_month_revenue | pass |
| official_market_daily | `xs_route_stale:official_market_daily` (actual_max_date=2026-10-02, required >= 2026-10-05); the declared gap 2026-09-30 is still present |
| official_finance_reporting_deadline | pass |
| deploy build (`load_or_build_cross_section`) | `xs_route_status:finmind_price_adj:PARTIAL`, as in probe_head_cccbca1f |

`xs_probe.py d1005` bypasses the PARTIAL status of price and PER in the scratch process only:
1. Without further bypass: `xs_route_stale:official_market_daily`.
2. With the board staleness also bypassed, for diagnosis only: the price and board reads pass and the 09-30 board gap resolves (present 2336, carried 0). The build then stops at `xs_board_missing_at_d` (2026-10-05), because official_market_daily has no 10-05 rows.
3. Counterfactual: even with 10-05 board rows, the board gate fails (`MIN_BOARD_ROWS_AT_D` TWSE 773 / TPEx 643; ratio >= 0.79):
   - TWSE has 300 four-digit price rows at D, against the 773 minimum and a 0.27 ratio.
   - TPEx has 140, against the 643 minimum and a 0.16 ratio.
   - So the gates fail closed correctly on the incomplete 10-05.

P and bands at D=10-05 therefore cannot be produced on this head. The closest comparison is the D=10-02 rebuild in section 6.

## 8. Implications

- **Do not relax the price PARTIAL rule to accept this head.** 10-05 is about 19 % complete, the entry declares no dated gap for it, and official_market_daily ends at 10-02. The existing gates would catch it, but only further down the chain.
- The history in the new object is **better** than the efbd4844 history used for the 10-02 verification. Once a complete head arrives, expect the 10-02-to-next-build differences described in section 6, plus whatever 10-05 itself brings.
- **For codex (updater):**
  1. expected_asof 10-05 was fixed at run start (04:51) and the 10-05 units were fetched at 05:2x, before the session. The resume at 22:05 republished those payloads instead of refetching. This is evidence from payload mtimes and `resume_identity.json`.
  2. The repair `bounds` rule permanently refuses 133 stocks whose stored first row (the listing-day artefact, open = 0) is never returned again. A refused stock gets no new dates, so these stocks are stuck. Examples: 7631 (missing its 09-29 adjustment) and 1294.
  3. Exact float equality turns provider rounding noise (1e-6..1e-3) into whole-stock refusals: 408 of the 700 step-1 conflicts.
  4. Step 1 requested history from 2021-10-05 while the store begins 2021-10-04. That alone refused 567 stocks.
  5. Whole-market per-date snapshots are mixed-vintage by nature: windows are not re-adjusted after later events, and no-trade placeholders copy later bars. The per-stock range series is internally consistent.
  6. The price entry should declare 10-05 as a dated gap, or not claim actual_max_date 10-05, when only 540 of 2,819 securities have it.

## 9. Files

- Scripts in `data/scripts/`:
  - parquet conversion (read-only): `to_parquet.py`, `to_parquet_pretty.py`, `omd_to_parquet*.py`
  - head and coverage inspection: `chain_routes.py`, `entry_dump.py`, `coverage_units.py`
  - object diffs: `overview.py`, `diff_price.py`, `classify_price.py`, `row_breakdown.py`, `diff_per.py`
  - consistency checks: `factor_consistency*.py`, `exdate_check.py`, `notrade_fill.py`
  - 10-05 and repair analysis: `d1005.py`, `cand_adoption.py`, `nonadopted.py`, `adjudicate.py`
  - xs: `xs_probe.py` (deploy-copy probe), `compare_xs.py`, `compare_xs2.py`
- Outputs in `data/out/`:
  - `chain_routes.txt`, `entry_*`
  - `diff_*.json` / `*.parquet`, `classify_*.csv`, `row_breakdown.json`, `diff_per.json`
  - `factor_consistency*.json`, `jumps2*_*.csv`, `exdate_check.json`, `notrade_fill.json`, `adjudicate.json`
  - `d1005_coverage.json`, `cand_adoption.csv`, `nonadopted_133.csv`
  - `compare_xs_1002*.json`
- Probes in `data/xs/{checks,d1005,d1002_new,d1002_old}/summary.json`; the two builds also export `xs_rows.json` and `band_tables.json`.
