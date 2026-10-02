"""Pure demand-contract checks; this test file is not run during implementation."""
import unittest

from analysis_data_demand import build_data_plan, resolve_feature_requirements
from mix_price_features import (
    MAX_FINITE_FEATURE_LOOKBACK_SESSIONS,
    MIX_FEATURE_COLUMNS,
    mix_feature_formula_registry,
)
from supplemental_features import SUPPLEMENTAL_FEATURE_COLUMNS
from top10_data_demand_specs import (
    LEGACY_FEATURE_ORIGIN,
    ORDER21_ROUTE,
    PRICE_ROUTE,
    order21_report_requirement,
    top10_feature_specs,
)


class Top10DemandSpecTests(unittest.TestCase):
    def test_formula_registry_covers_every_model_column_and_longest_finite_path(self):
        formulas = mix_feature_formula_registry()
        self.assertEqual({"mix_" + name for name in formulas}, set(MIX_FEATURE_COLUMNS))
        self.assertEqual(MAX_FINITE_FEATURE_LOOKBACK_SESSIONS, 179)

    def test_windows_share_formula_registry_and_use_supported_units(self):
        specs = top10_feature_specs(MIX_FEATURE_COLUMNS, SUPPLEMENTAL_FEATURE_COLUMNS,
                                    origin_start="2021-09-29")
        self.assertEqual(specs["schema_version"], "analysis_data_demand.v1")
        self.assertEqual(
            specs["features"]["mix_sma_slope_120"]["operations"],
            [
                {"kind": "rolling", "count": 120, "unit": "sessions"},
                {"kind": "rolling", "count": 60, "unit": "sessions"},
            ],
        )
        self.assertEqual(specs["coverage"]["minimum_feature_sessions"], 21)
        self.assertEqual(specs["coverage"]["desired_feature_lookback_sessions"], 179)

    def test_stateful_seed_is_distinct_from_finite_warmup(self):
        specs = top10_feature_specs(MIX_FEATURE_COLUMNS, SUPPLEMENTAL_FEATURE_COLUMNS,
                                    origin_start="2021-09-29")
        resolved = resolve_feature_requirements(["mix_ema_distance_120"], specs)
        price = resolved[PRICE_ROUTE]
        self.assertEqual(price["coverage"]["minimum_observations"], 21)
        self.assertEqual(price["coverage"]["desired_warmup_count"], 179)
        self.assertEqual(price["formula_lookback_count"], 1)
        self.assertEqual(price["lookback_count"], 1)
        self.assertTrue(price["recursive"])
        self.assertEqual(price["prefix"], {"unit": "sessions", "start_date": "2021-09-29"})
        self.assertFalse(price["carry_in"])

        finite_sma = resolve_feature_requirements(["mix_sma_slope_120"], specs)[PRICE_ROUTE]
        self.assertEqual(finite_sma["formula_lookback_count"], 179)
        self.assertEqual(finite_sma["lookback_count"], 179)
        self.assertFalse(finite_sma["recursive"])
        self.assertFalse(finite_sma["prefix"])

        plan = build_data_plan(
            "fixture", "daily_frozen_inference", "2026-09-29", {},
            ["mix_ema_distance_120"], specs,
            sample_start="2025-09-29",
            extra_requirements={"routes": {PRICE_ROUTE: {
                "unit": "sessions", "start_date": "2021-09-29",
                "end_date": "2026-09-29",
            }}},
        )
        planned_price = plan["routes"][PRICE_ROUTE]
        self.assertEqual(planned_price["query_strategy"], "explicit_prefix_start_date")
        self.assertEqual(planned_price["query_before_count"]["count"], 0)
        self.assertTrue(planned_price["retention"]["retain_seed_prefix_until_features_built"])
        self.assertEqual(
            planned_price["retention"]["post_feature_trim_start_date"],
            "2025-09-29",
        )
        computation = specs["features"]["mix_ema_distance_120"]["computation"]
        self.assertTrue(computation["stateful"])
        self.assertFalse(computation["persisted_carry_in_state"])
        self.assertEqual(computation["origin_policy"]["calendar_years"], 5)
        self.assertEqual(specs["legacy_feature_origin"], LEGACY_FEATURE_ORIGIN)
        self.assertEqual(LEGACY_FEATURE_ORIGIN["calendar_years"], 5)
        self.assertEqual(LEGACY_FEATURE_ORIGIN["anchor"], "descriptor.actual_max_date")

    def test_omitted_parent_features_still_expand_rank_and_interaction_formulas(self):
        specs = top10_feature_specs(["mix_cs_rank_return_20"], [],
                                    origin_start="2021-09-29")
        resolved = resolve_feature_requirements(["mix_cs_rank_return_20"], specs)
        self.assertIn(PRICE_ROUTE, resolved)
        self.assertTrue(resolved[PRICE_ROUTE]["all_symbols"])
        self.assertEqual(resolved[PRICE_ROUTE]["formula_lookback_count"], 21)

        interaction = resolve_feature_requirements(
            ["mix_interaction_oversold_mean_reversion"], specs
        )
        self.assertEqual(interaction[PRICE_ROUTE]["formula_lookback_count"], 20)

    def test_window_edges_and_streak_are_registered_as_formula_parents(self):
        specs = top10_feature_specs(MIX_FEATURE_COLUMNS, SUPPLEMENTAL_FEATURE_COLUMNS,
                                    origin_start="2021-09-29")
        formulas = specs["features"]
        self.assertEqual(
            formulas["mix_signed_return_streak_10"]["dependencies"],
            [
                {"feature": "mix_consecutive_up_sessions_10"},
                {"feature": "mix_consecutive_down_sessions_10"},
            ],
        )
        self.assertEqual(
            resolve_feature_requirements(["mix_up_ratio_20"], specs)[PRICE_ROUTE][
                "formula_lookback_count"
            ],
            21,
        )
        self.assertEqual(
            resolve_feature_requirements(["mix_recovery_from_20d_low"], specs)[
                PRICE_ROUTE
            ]["formula_lookback_count"],
            20,
        )

    def test_unselected_supplemental_routes_are_not_requested(self):
        specs = top10_feature_specs(["mix_return_1"], [], origin_start="2021-09-29")
        resolved = resolve_feature_requirements(["mix_return_1"], specs)
        self.assertEqual(set(resolved), {PRICE_ROUTE})

    def test_order21_remains_separate_report_only_full_history_demand(self):
        requirement = order21_report_requirement()
        self.assertEqual(requirement["route"], ORDER21_ROUTE)
        self.assertEqual(requirement["unit"], "sessions")
        self.assertTrue(requirement["full_history"])
        self.assertEqual(requirement["display_windows"], [1, 5, 20, 60])

    def test_unknown_feature_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unsupported_top10_core_feature"):
            top10_feature_specs(["mix_unknown_feature"], [], origin_start="2021-09-29")


if __name__ == "__main__":
    unittest.main()
