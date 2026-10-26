# Why head cccbca1f has so many PARTIAL routes (run ad5ea647 resume)

Scope: read-only analysis of live `D:/TWStockUpdater/src/twstock_updater` (all source mtimes are before the
22:05 resume start, so the live code is the code that ran the resume), the shared manifests chain
`efbd4844 -> cccbca1f` (20 publications), and `D:/TWStockData/updater_runtime/runs/20261004T205159Z-ad5ea647`
(receipts, canonical payloads, http_cache) plus `control/price_vintage_repair.json` and `logs/update_errors.jsonl`.
Nothing on D: was written. The only program code run was a pure function (`runner._demand_source_evidence`) under dguard
(0 violations).

## 1. Short answer

* **The coverage is not complete.** The `attempted=success=700` on `finmind_price_adj` is the coverage of the
  same-run *vintage-repair* candidate (700 stocks re-fetched), not route coverage. It has no `total`. The route's last
  full candidate is preserved as `prior_coverage`: 1213 of 1214 sessions, `missing_dates ['2026-10-05']`. The other
  FinMind daily routes show 999 of 1000 sessions with `missing_dates ['2026-10-05']`.
* **What triggered it.** Run ad5ea647 started at **04:51 local on Mon 10-05**. `expected_asof` defaults to today's
  Taipei date, so 10-05 was the target. Every "today" request returned an EMPTY body between 04:53 and 05:17 (before the
  session), so the candidates came out PARTIAL. The 22:05 resume kept `expected_asof=2026-10-05` and did **not**
  re-fetch the dependent routes:
  * Their retry was **BLOCKED** (`route_demand_deferred:await_published_price_axis`).
  * `run_finalize._should_protect_existing_route` then re-persisted the 05:xx payloads with status PARTIAL.
  * Those PARTIAL payloads were published. This also turned two routes that had complete data
    (`global_market_history`, `finmind_government_bank`) from SUCCESS to PARTIAL.
* **Why the price axis was "unproven".** The same-run price vintage repair publication (2420b139, 22:37) **replaced the
  price entry's metadata** with the repair candidate's thin metadata: coverage {attempted, success}, no `total`, no
  source receipts, no history coverage. That erased the proof that dependent routes need. I checked this by evaluating
  `runner._demand_source_evidence` on the price entry of each head:
  * efbd4844: prefix_complete=True (as of 10-02).
  * 0bf8d15c, 2420b139 and cccbca1f: prefix_complete=False.
* **finmind_price_adj itself cannot clear under the current code.** 133 stocks are refused on every attempt
  (`vintage_history_conflict`):
  * The store holds a leading listing-day row for each of them that FinMind no longer returns, neither in whole-market
    single-day responses nor in per-stock range responses.
  * The vintage rule demands `candidate_first_date <= stored_first_date`, which can never be met.
  * These 133 stocks are in `control/price_vintage_repair.json`, so every future run re-injects them before persisting
    the price route (`apply_repair`) and gets the conflict again.
  * In addition, a same-run repair publication can never satisfy the PARTIAL-recovery gate, because the repair
    candidate has no `coverage.total`.
* **Verdict.** This is not just "resume being a resume". The early-morning target date was the trigger. The
  persistence is caused by updater defects: D1 to D4 below are defects, D5 is a design trap, D6 and D7 are secondary.
* **What clears it.**
  * No updater action under the current code clears `finmind_price_adj`.
  * Resuming ad5ea647 again will not help.
  * After codex fixes D1 and D2, one **new** `update` run (not a resume) whose `expected_asof` is a session FinMind has
    already published should restore SUCCESS. Examples: `update --mode incremental --expected-asof 2026-10-05` run now,
    or a default run started after that day's evening publication. On this resume's timings the run would take about
    3 to 3.5 h, dominated by the merges.

## 2. Timeline of run 20261004T205159Z-ad5ea647 (local time, +08:00)

| time | event | evidence |
|---|---|---|
| 10-05 04:51 | run created; resume_identity: mode full-backfill, expected_asof **2026-10-05** | `resume_identity.json`; run id |
| 04:53-05:17 | the "2026-10-05" request of each daily route returns an EMPTY 40-byte body (`{"data":[]}`); 92 EMPTY bodies sit in the run http_cache (observed 04:xx-05:xx) | canonical payload receipts, e.g. per 05:04:47, price 05:11, institutional 05:16; http_cache metadata |
| 07:52 | head efbd4844 published (only calendar, deadline, cash-flow, futures_institutional, block_trade from ad5ea647; futures and block_trade already PARTIAL with the same `missing 2026-10-05` symptom) | manifests |
| 08:06 | prepared merge object e35ef242 (price) written, not published | `_objects/e35ef242...` mtime |
| 22:05-22:06 | resume (`cli resume` path: keep_completed, retry_incomplete, expected_asof from identity = 10-05); order 8 retry hits `SharedPublicationBusy` and is BLOCKED (protected) | receipt 08 `la=BLOCKED formal_coverage_invalid:[Errno 16]` |
| 22:25 | **0bf8d15c**: price main candidate published (05:33 payload, 1213/1214, missing 10-05). 700 stocks refused (`vintage_history_conflict`), status PARTIAL | manifest |
| 22:25-22:31 | vintage repair fetches 700 per-stock series 2021-10-04..2026-10-05 (FinMind already serves 10-05: 673 rows) | 702 non-empty http_cache entries at 14:xxZ |
| 22:37 | **2420b139**: repair published. 567 accepted, 133 refused again. Warnings `prior_partial_unresolved, vintage_history_conflict`. Metadata replaced by the repair candidate's | manifest; control file written 22:37:56 with 133 stocks |
| 22:39-22:43 | retries of orders 6,7,15-18,20,22-24 all BLOCKED `await_published_price_axis` and re-persisted as PARTIAL from the 05:xx payloads | receipts (status PARTIAL, previous_status, last_attempt BLOCKED) |
| 23:01-01:21 | those PARTIAL candidates published one by one (institutional 23:01 ... government_bank 00:56 ... margin_maintenance 01:21) | chain.txt |
| 01:22-01:23 | orders 3,19,29-32 retried, BLOCKED the same way, published PARTIAL; cccbca1f at 01:23:18 | receipts mtime 01:22; manifests |
| 01:23:22 | run aborts: `SharedPublicationBusy` in `_formal_coverage_for_route -> make_existing_published_reference -> _lock` | `logs/update_errors.jsonl` |

The run is **not finished**: there is no `run_summary.json` and the order-21 phase never ran.

## 3. Exact semantics of the two warnings

### 3.1 `vintage_history_conflict` (finmind_price_adj only)
`stock_shared_store._merge`, stage "比對重疊資料", lines 4391-4421. For every stock that has candidate rows (side 1)
and stored rows (side 0):

1. If at least one natural key overlaps and **none** of `close, open, high, low, max, min` differs on the overlap,
   the stock goes through the normal per-row merge.
2. Otherwise (any OHLC difference on the overlap, **or no overlap at all**), the whole stored series is replaced only
   when **both** of these hold:
   * (a) Newer-stamp test: every candidate row has a tz-aware `_shared_collected_at`, and the earliest candidate stamp
     is strictly later than the latest stored stamp. Stored rows without a stamp count as older.
   * (b) Bounds test: candidate `min(date) <= stored min(date)` **and** candidate `max(date) >= stored max(date)`.
3. If either test fails, all candidate rows of that stock are deleted, the stock is appended to
   `vintage_conflicts`, and `vintage_history_conflict` is added to the warnings.

Any warning makes the merged entry PARTIAL (line 4786: `if (old and warnings) ... status = 'PARTIAL'`). The
publication report carries `vintage_conflict_stocks`. After that:

* `price_vintage_repair.repair_after_publication` (runner 4287-4313) records the stocks in
  `control/price_vintage_repair.json`, re-fetches their per-stock series from the store's `actual_min_date` to
  `expected`, and publishes them as a second candidate in the same run.
* Stocks that are still refused stay pending.
* The next run injects the pending stocks' full series into its own price result before persisting it
  (`apply_repair`, runner 3736).

### 3.2 `prior_partial_unresolved`
It is added in three places.

* **Main merge path** (lines 4654-4745). This path is taken when the old entry is not SUCCESS, `complete` or
  `available`.
  * For non-order21 routes the merge is "recovered" only if **all** of these hold:
    * The candidate status is `SUCCESS`.
    * `coverage.total` is a finite number > 0 and `coverage.covered` (or `success`) equals it (`complete_units`, line
      4660). The government_bank pending-release scope is a special case.
    * `_partial_recovery_scope(metadata)` is not None.
    * The candidate `gaps == []` and `errors == []`.
    * **No warning has been raised so far in this merge.**
    * Every prior gap is resolved or superseded by `_resolve_prior_gaps`.
  * If the merge is not recovered, `prior_partial_unresolved` is added (line 4744), and the entry stays PARTIAL through
    line 4786.
  * `partial_recovery.status` becomes `unresolved` even when all gap lists are empty. The actual reason
    (`complete_units` False, or other warnings present) is **not recorded**. That is why the head shows "unresolved" with
    empty lists.
  * Order21 has its own variant of this check.
* **No-data / same-body completion path** (lines 3800-3825). This path is not reachable for finmind_price_adj
  (`_same_body_completion_update` excludes it).
  * When the old entry is PARTIAL, every prior warning other than `prior_partial_unresolved` becomes an unresolved
    reason.
  * If the old entry has no recorded gaps, `prior_partial_cause_unknown` is added.
  * Any of these adds `prior_partial_unresolved`.
* **Current-native-scope path** (lines 3374-3395). This path *removes* the warning when the prior PARTIAL was only
  `current_native_scope_pending`.

## 4. finmind_price_adj step by step (replicated)

I replicated the vintage rule offline (`vintage_main.py`, `vintage_check.py`). The numbers match the published heads
exactly.

* **0bf8d15c (main resume publish).**
  * Inputs: old efbd4844 object a5fe7c55 (SUCCESS), candidate `payloads/05-finmind_price_adj.json` (78eac9cc,
    whole-market single-day 2021-10-05..2026-10-05, 10-05 EMPTY).
  * Result per stock: 2,182 equal overlap, 91 replaced, **700 refused**.
  * All 700 refusals have newer=True and covers=False:
    * **567** because the store starts at **2021-10-04** while the candidate window starts at 2021-10-05. The rolling
      5-year cutoff advanced one day, and retention trimming did not remove 2021-10-04.
    * **133** because the store holds the stock's first (listing-day) row and the provider response no longer
      contains it (for example 00956 stored 2024-08-20 close 9.92, candidate starts 2024-08-21).
  * All 700 also had OHLC differences, meaning a new adjustment vintage. 398 of them differ from the first overlap day
    (2021-10-05).
  * The candidate itself was PARTIAL (missing 10-05), and the warning was `vintage_history_conflict`, so the entry
    became PARTIAL. The old entry was SUCCESS, so there was no recovery step and no `prior_partial_unresolved` yet.
* **2420b139 (same-run repair).**
  * Candidate: 700 per-stock series 2021-10-04..2026-10-05, coverage `{attempted:700, success:700}`.
  * **567 accepted** (bounds now covered). **133 refused again**: for every one of them the per-stock series also
    starts one session after the stored first row (`('cand_starts_later',): 133`).
  * The old entry was PARTIAL, so the recovery gate ran. `complete_units` is False because there is no
    `coverage.total`, and the merge already had a `vintage_history_conflict` warning. The merge was not recovered, so
    `prior_partial_unresolved` was added. Last-attempt warnings:
    `['prior_partial_unresolved','vintage_history_conflict']`.
  * `lineage = dict(candidate.metadata)` (line 4598), so the published metadata is now the repair's: coverage 700/700,
    `expected_asof 2026-10-05`, `requested_from_date 2021-10-04`, no source receipts. `prior_coverage` keeps the 0bf8d15c
    coverage (`missing_dates ['2026-10-05']`).
* **No later head touched price.** cccbca1f has the same object fbb9fb50 (3,073,417 rows).

**Content warning for consumers:**
* The head says `actual_max_date 2026-10-05`, but only **540 stocks** have a 2026-10-05 row. For comparison, 2,819 rows
  are dated 2026-10-02.
* 133 stocks keep an older adjustment vintage.

The 133 pending stocks are listed in `control/price_vintage_repair.json` (ETFs such as 00956, 00960, 00967B, 00981B;
ETNs 020029-020041; stocks 1293, 1294, 1295, 1480, 2254, ...). Detailed bounds for each stock are in
`vintage_detail.json` and `vintage_main_detail.json`.

## 5. Why the other routes became PARTIAL

* **Two separate mechanisms combine:**
  1. **Pre-session target.** The original 05:xx candidate was PARTIAL:
     * Session units: 999/1000, `missing_dates ['2026-10-05']`.
     * Range units (exchange_rate, bonds, crude, gold, futures/options institutional, active ETF): the tail request
       `2026-10-04..2026-10-05` came back EMPTY, so the unit was unproven.
  2. **Resume protection.** The resume's retry was BLOCKED (`await_published_price_axis`). `_should_protect_existing_route`
     (run_finalize 680-688, 983-1010) keeps the earlier payload but writes `status: PARTIAL` (`previous_status` =
     earlier status), and runner then publishes it.
     * For `global_market_history` (26/26 instruments OK) and `finmind_government_bank` (1000/1000, due scope complete),
       the earlier status was **SUCCESS**. Their PARTIAL comes purely from this protection rule.
* **No merge warnings.** In the merge the old entry was SUCCESS, so no recovery step ran and `status = candidate.status`
  (line 4626). These entries therefore carry **no warnings** (`last_attempt.warnings == []`). Their PARTIAL is the
  candidate's own status, not `prior_partial_unresolved`.
* **Data content is mostly unchanged.** Record counts and date ranges equal efbd4844 for institutional, margin, per,
  shareholding, day_trading, loan_collateral and margin_maintenance (max 10-02). Object hashes changed because rows were
  re-merged; I did not compare values.
* **Why the retry was BLOCKED.**
  * `_run_route_phases` (runner 3806-3832) runs dependent routes (`native_prefix`, `calendar_month_periods`) against
    the published price axis.
  * `consumer_route_demand.build_mix_sample_context` needs `price_evidence.coverage.prefix_complete`.
  * `_demand_source_evidence` derives that from `coverage.history_complete` / `prefix_complete`, or from
    single-day SUCCESS receipts covering every session.
  * After 2420b139, the price entry has neither, so the result is `price_axis_unproven` and the dependents get
    `await_published_price_axis`.
  * Simulated with the session axis from the receipts plus 2026-10-05: efbd4844 True (as of 10-02), 0bf8d15c False
    (10-05 receipt EMPTY), 2420b139 and cccbca1f False (0 receipts).
* **Why re-running the same run cannot fix the target day.** Within the same run, `run_io._cached_response` replays
  FinMind bodies with `require_nonempty=False` (run_io 929; only cross-run reuse requires non-empty data). Source-maturity
  gating applies only to the filing-release families. So even an unblocked retry inside ad5ea647 would replay the
  05:xx EMPTY 10-05 body.

## 6. Expected behaviour or defect?

| id | finding | judgement | confidence |
|---|---|---|---|
| D1 | The same-run price vintage repair publishes a candidate whose metadata (coverage `{attempted,success}`, no `total`, no receipts) **replaces** the route metadata. (a) It can never pass the PARTIAL-recovery gate, so it always adds `prior_partial_unresolved`. (b) It erases the price-axis proof, so every `native_prefix`/`calendar_month_periods` route is deferred afterwards. | defect | high (code + receipts + simulation) |
| D2 | The vintage bounds test needs the candidate to start on or before the stored first date. The store keeps listing-day rows the provider no longer serves, so 133 stocks are refused permanently and stay in the pending file, and every future run re-injects them. | defect | high (exact replication) |
| D3 | The main candidate window starts at the rolling cutoff (2021-10-05) while the store keeps 2021-10-04, because retention trimming only advances on a successful scope. Any re-adjusted stock that has the oldest row conflicts in the main merge (567 here). The repair fixes the data in the same run, but the head is still PARTIAL. | defect / design gap | high |
| D4 | Resume protection turns a BLOCKED retry into a PARTIAL candidate built from the old payload, and runner publishes it. This downgrades SUCCESS routes and republishes stale pre-session payloads. | defect | high |
| D5 | `expected_asof` defaults to today's date; session units with key == expected are "due" (published_coverage 651 marks only key > expected as not_due). A run started before that day's FinMind publication always yields PARTIAL. The resume keeps that `expected_asof`, and the same-run cache replays the EMPTY bodies. | design trap (trigger) | high |
| D6 | `SharedPublicationBusy`: `_lock` waits 0 ms on a named mutex. It fired at 22:06 (order 8, BLOCKED) and fatally at 01:23:22, 4 s after the last publication. Likely the main thread racing the publication worker thread (`publication_worker.py`); this is not verified. The run ended unfinished. | probable defect | medium |
| D7 | `_shared_collected_at` is stamped with the reusing run's start time even for cross-run cached responses (main price rows retrieved 10-04 11:13Z were stamped 20:51:59Z). This contradicts the `_run_collected_at` docstring, and the vintage newer-stamp test depends on it. | provenance defect | high (fact), low (impact here) |

## 7. What would clear it

* **finmind_price_adj.** No current updater action clears it. Both a new run and a resume will re-refuse the 133
  stocks (D2), and the same-run repair cannot recover (D1). It needs a codex fix to the vintage rule or the repair.
  Possible directions:
  * Accept a full-series candidate when stored-only dates precede the provider-served range.
  * Give the repair candidate real coverage, or merge its metadata instead of replacing it.
  * Trim to the cutoff.
* **Dependent routes** (institutional, margin, per, shareholding, day_trading, futures/options institutional,
  government_bank, block_trade, loan_collateral, margin_maintenance, exchange/bonds/crude/gold, global_market_history).
  They need a fresh collection of a session that is already published **and** a proven price axis when their demand is
  resolved.
  * Predicted from code, not executed: as long as D1 is unfixed, a new run publishes price first, then the same-run
    repair erases the axis proof. The dependents would be BLOCKED, not published, and keep today's PARTIAL entries.
* **Active ETF holding/change.** Not price-axis dependent. Their PARTIAL comes from pre-session tails. A new run with an
  already-published `expected_asof` should clear them (not executed).
* **Resuming ad5ea647.** It does not help: `expected_asof` stays 10-05, dependents are deferred again, the same-run cache
  would replay the EMPTY 10-05 bodies, and D6 can abort it again.
* **After the codex fix.** Run one `update --mode incremental` as a **new** run with `--expected-asof` set to a session
  whose FinMind data is already out (2026-10-05 data was served by 22:25 on 10-05), or start a default run after the
  evening publication rather than at 04:51.
  * Duration, from this resume: 22:06 to 01:23 is about 3 h 17 min of publication. The price main publish took about
    20 min, the repair about 12 min, the large dependents 8-25 min each, and government_bank about 75 min. Expect about
    3-3.5 h end to end. This is an estimate.
* **Pre-existing PARTIALs.** `official_market_daily` (2026-09-30 gap), `tdcc_share_distribution_raw`,
  `official_sector_observations` and `finmind_trading_daily_report` are unchanged from efbd4844 and unrelated to this
  run.

## 8. Every route: efbd4844 vs cccbca1f

"same obj" means the identical object sha256. "Protected" means the run_finalize protection re-persisted the earlier
payload as PARTIAL after a BLOCKED retry with reason `await_published_price_axis`.

| order | route | efbd4844 (source run) | cccbca1f (source run) | new last_attempt warnings | reason now |
|---|---|---|---|---|---|
| 0 | official_calendar | SUCCESS (ad5ea647) | SUCCESS, same obj | - | unchanged |
| 1 | official_market_daily | PARTIAL (9a26f992) | PARTIAL, same obj | incomplete_replacement_retained, prior_partial_unresolved | unchanged; prior gaps 2026-09-30 TWSE/TPEx FAILED, Emerging EMPTY |
| 2 | official_trading_restrictions | SUCCESS (424b9768) | SUCCESS, same obj | - | resume attempt BLOCKED, not published |
| 3 | global_market_history | SUCCESS (9a26f992) | **PARTIAL** (ad5ea647) | [] | protected: 05:36 payload had 26/26 OK, previous_status SUCCESS; old SUCCESS so candidate status copied |
| 4 | finmind_stock_info | SUCCESS (424b9768) | SUCCESS, same obj | - | unchanged |
| 5 | finmind_price_adj | SUCCESS (9a26f992) | **PARTIAL** (ad5ea647) | prior_partial_unresolved, vintage_history_conflict | section 4: 133 permanent conflicts; repair metadata has no total; 10-05 rows for 540 stocks only |
| 6 | finmind_institutional | SUCCESS (9a26f992) | **PARTIAL** (ad5ea647) | [] | 999/1000, missing 10-05 (EMPTY 05:16); protected |
| 7 | finmind_margin | SUCCESS | **PARTIAL** | [] | missing 10-05 (05:06); protected |
| 8 | official_finance_reporting_deadline | SUCCESS (ad5ea647) | SUCCESS, same obj | - | retry BLOCKED by SharedPublicationBusy at 22:06; entry unchanged |
| 9 | finmind_month_revenue | SUCCESS (9a26f992) | SUCCESS, same obj | - | resume BLOCKED (calendar_month_periods), not published |
| 10 | finmind_financial_statements | SUCCESS | SUCCESS, same obj | - | unchanged |
| 11 | finmind_balance_sheet | SUCCESS | SUCCESS, same obj | - | unchanged |
| 12 | finmind_cash_flows_statement | SUCCESS (ad5ea647) | SUCCESS, same obj | [] | unchanged (last_attempt PARTIAL original_observation_unknown, baseline preserved) |
| 13 | finmind_dividend | SUCCESS | SUCCESS, same obj | - | run SKIPPED |
| 14 | finmind_dividend_result | SUCCESS | SUCCESS, same obj | - | run SKIPPED |
| 15 | finmind_per | SUCCESS (9a26f992) | **PARTIAL** (ad5ea647) | [] | missing 10-05 (05:04); protected |
| 16 | finmind_shareholding | SUCCESS | **PARTIAL** | [] | missing 10-05 (05:08); protected |
| 17 | finmind_day_trading | SUCCESS | **PARTIAL** | [] | missing 10-05 (05:05); protected |
| 18 | finmind_futures_institutional | PARTIAL (ad5ea647, 07:52) | PARTIAL, same obj | [] | range unit 2022-08-19..10-05 unproven (tail EMPTY 04:53) |
| 19 | finmind_options_institutional | SUCCESS (9a26f992) | **PARTIAL** (ad5ea647) | [] | range tail EMPTY 05:12; protected; +6 rows |
| 20 | finmind_government_bank | SUCCESS (9a26f992, resolved_for_scope) | **PARTIAL** (ad5ea647) | [] | 1000/1000 due complete (2023-01-11 provider-unavailable); protected, previous_status SUCCESS |
| 21 | finmind_trading_daily_report | PARTIAL (424b9768) | PARTIAL, same obj | prior_partial_unresolved, stored_duplicate_natural_key | unchanged; order21 phase not run (aborted) |
| 22 | finmind_block_trade | PARTIAL (ad5ea647, 07:52) | PARTIAL, same obj | [] | missing 10-05 (05:04) |
| 23 | finmind_loan_collateral | SUCCESS | **PARTIAL** | [] | missing 10-05 (05:17); protected |
| 24 | finmind_margin_maintenance | SUCCESS | **PARTIAL** | [] | missing 10-05 (05:15); protected |
| 25 | official_active_etf_holding | SUCCESS (424b9768) | **PARTIAL** (ad5ea647) | [] | 4/40 ETF units; 36 tails 10-04..10-05 EMPTY 05:08-05:10; not re-attempted |
| 26 | official_active_etf_change | SUCCESS (424b9768) | **PARTIAL** (ad5ea647) | [] | 0/40; 40 tails EMPTY; not re-attempted |
| 27 | finmind_industry_chain_flow | SUCCESS | SUCCESS, same obj | - | unchanged |
| 28 | official_sector_features | SUCCESS | SUCCESS, same obj | - | run EMPTY, not published |
| 29 | finmind_exchange_rate | SUCCESS (9a26f992) | **PARTIAL** (ad5ea647) | [] | range tail EMPTY 05:10; protected |
| 30 | finmind_government_bonds_yield | SUCCESS | **PARTIAL** | [] | same as 29 |
| 31 | finmind_crude_oil_prices | SUCCESS | **PARTIAL** | [] | same as 29 |
| 32 | finmind_gold_price | SUCCESS | **PARTIAL** | [] | range unit 2022-08-19..10-05 0/1 (first cached attempt FAILED; second 287,641 rows at 05:10 but unit unproven); protected |
| 33 | tdcc_share_distribution_raw | PARTIAL (9a26f992) | PARTIAL, same obj | prior_partial_unresolved | unchanged (unmapped HoldingSharesLevel gaps) |
| 34 | news_and_hot_topics_raw | SUCCESS | SUCCESS, same obj | - | resume BLOCKED, not published |
| 35 | official_sector_observations | PARTIAL (f5549d9e) | PARTIAL, same obj | source_timestamp_unknown_or_not_newer | unchanged |

Totals: efbd4844 has 30 SUCCESS and 6 PARTIAL; cccbca1f has 13 SUCCESS and 23 PARTIAL. 17 routes flipped from SUCCESS
to PARTIAL.

## 9. Consumer note (DStock)
The three-month gate `xs_route_status:finmind_price_adj:PARTIAL` is correct to fail closed:
* Only 540 stocks have the 10-05 price row.
* 133 stocks are on an older vintage.

If the gate also checks institutional, margin, per, shareholding and the other dependent routes, those are PARTIAL too
(their content is still up to 10-02). Whether a consumer should accept known, enumerated PARTIAL causes (for example
excluding the 133 stocks) is a policy decision for the user, not something the updater evidence settles.

## 10. Files in this folder
* `chain.txt`: per-publication route changes from efbd4844 to cccbca1f.
* `vintage_main.py` / `vintage_main_detail.json`: replication of the 0bf8d15c vintage decisions.
* `vintage_check.py` / `vintage_detail.json`: replication of the 2420b139 repair decisions (133 refused).
* `sim/price_axis_sim.py`, `sim/price_axis_sim2.py`: `_demand_source_evidence` on each head (run under dguard, 0
  violations).
* `dump_entry.py`, `slim.py`, `price_adj_entries.txt`: entry dumps.

## 11. Open questions for codex
* Is D6 (the mutex race between the main thread and the publication worker) the real cause of the 01:23:22 abort?
* Exactly which collector or runner step marks `global_market_history` PARTIAL? The receipts point to the protection
  path; the collector itself reported SUCCESS.
* Why did the first phase (04:51-08:06) stop without publishing the prepared price merge? No log is available.
* Should the protection rule (D4) ever publish a PARTIAL built from a stale payload, rather than retaining the head
  entry?
