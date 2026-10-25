"""Fast offline checks for editing the model: python -m unittest tests.test_model."""
import unittest
from unittest.mock import patch

import pandas as pd
import numpy as np

from app import data, engines, restoration, risk, rules, weather
from app.models import AssessResponse, Scenario


class ModelTests(unittest.TestCase):
    def test_both_risk_models_train_and_explain(self):
        store = data.Store(report={"arcos": {"status": "ok"}})
        store.circuits = pd.DataFrame({
            "n_customers": np.arange(100, 130), "primary_miles": np.arange(1, 31, dtype=float),
            "pre_records": np.arange(30) % 7, "ref_records": np.arange(30) % 2,
        })
        store.circuits.loc[0, "primary_miles"] = np.nan
        for kind in ("logistic_regression", "random_forest"):
            with self.subTest(kind=kind):
                model = risk.train_risk(store, kind)
                self.assertTrue(model.ok)
                self.assertIn("simpleimputer", model.pipe.named_steps)
                self.assertEqual(len(model.test_scores), 30)
                self.assertTrue(np.isfinite(model.test_scores).all())
                score = risk.area_score(model, {"primary_miles": 25, "n_customers": 125, "hist_records": 6})
                self.assertTrue(0 <= score["p_storm"] <= 1)
                self.assertTrue(0 <= score["percentile"] <= 1)
                self.assertTrue(score["contribs"])
                if kind == "random_forest":
                    self.assertTrue(all("replaced" in text for _, _, text in score["contribs"]))

    def test_incompatible_risk_tables_fall_back(self):
        state = engines.State(data.mock_store({}), risk.RiskModel(kind="random_forest"), "mock",
                              {"analog": {**risk.FALLBACK_ANALOG, "exposure_model": "logistic_regression"}})
        self.assertIs(engines._analog_of(state), risk.FALLBACK_ANALOG)

    def test_risk_band_boundaries(self):
        self.assertEqual(risk.risk_band_for(risk.LOW_BELOW / 2), "low")
        self.assertEqual(risk.risk_band_for(risk.LOW_BELOW), "medium")
        self.assertEqual(risk.risk_band_for(risk.MEDIUM_BELOW), "high")

    def test_restoration_is_repeatable_and_what_ifs_reproduce(self):
        scenario = Scenario(storm_severity=3, damaged_poles=6)
        result = restoration.estimate_restoration(risk.FALLBACK_ANALOG, scenario)
        self.assertEqual(result, restoration.estimate_restoration(risk.FALLBACK_ANALOG, scenario))
        interval = result["range"]
        self.assertLessEqual(interval["low_hours"], interval["median_hours"])
        self.assertLessEqual(interval["median_hours"], interval["high_hours"])
        self.assertAlmostEqual(sum(b["share"] for b in result["histogram"]), 1, delta=0.02)
        for change in result["deltas"]:
            with self.subTest(change=change["change"]):
                updated = Scenario(**{**scenario.model_dump(), **change["scenario_patch"]})
                actual = restoration.estimate_restoration(risk.FALLBACK_ANALOG, updated)
                self.assertEqual(actual["range"], change["new_range"])

    def test_severe_storm_takes_longer(self):
        mild = restoration.estimate_restoration(risk.FALLBACK_ANALOG, Scenario(storm_severity=1))
        severe = restoration.estimate_restoration(
            risk.FALLBACK_ANALOG, Scenario(storm_severity=5, damaged_poles=12))
        self.assertGreater(severe["range"]["median_hours"], mild["range"]["median_hours"])

    def test_medical_alert_requires_declared_need(self):
        plain = rules.build_rules("low", {"high_hours": 12}, Scenario(backup_hours=2))
        medical = rules.build_rules("low", {"high_hours": 12}, Scenario(
            declared_needs=["medical_equipment"], backup_hours=2))
        self.assertFalse(plain["alert_eligible"])
        self.assertTrue(medical["alert_eligible"])
        self.assertEqual(medical["customer_actions"]["channel"], "phone_call")
        self.assertIn("2 hours", medical["customer_actions"]["escalation"]["trigger"])

    def test_weather_features_use_today_and_previous_days(self):
        daily = pd.DataFrame({"gust": [10, 40, 20], "precip": [1, 2, 3]})
        last = weather.make_features(daily).iloc[-1].to_dict()
        self.assertEqual(last, {"gust": 20, "gust_max2": 40, "precip": 3,
                                "precip_prev": 2, "precip3": 6})

    def test_missing_weather_model_has_fallback(self):
        with patch.object(weather, "fetch_forecast") as fetch:
            answer = weather.live_weather(None, 0, 0)
        fetch.assert_not_called()
        self.assertTrue(answer["fallback"])
        self.assertIsNone(answer["severity"])
        self.assertTrue(answer["fallback_reason"])

    def test_assessment_contract_and_mock_fallback(self):
        store = data.mock_store({})
        state = engines.State(store, risk.RiskModel(), "mock")
        location = store.locations.iloc[0].location_id
        answer = engines.assess(state, location, Scenario(), "unit-test")
        validated = AssessResponse.model_validate(answer)
        self.assertTrue(validated.meta.fallback)
        self.assertTrue(validated.meta.fallback_reason)
        self.assertTrue(validated.meta.assumptions)
        self.assertTrue(validated.meta.limits)
        self.assertEqual(validated.confirmed, [])
        self.assertIsNone(engines.assess(state, "LOC-999", Scenario(), "unit-test"))


if __name__ == "__main__":
    unittest.main()
