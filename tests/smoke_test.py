"""Smoke test: hit every endpoint with a mild and a severe scenario.

Run:  python -m tests.smoke_test      (or: pytest tests/smoke_test.py)
"""
import json
import re
import time

from fastapi.testclient import TestClient

from app.main import app
from app.messages import validate

MILD = {"storm_severity": 1, "damaged_poles": 0, "crews": 4, "access_ok": True, "declared_needs": []}
SEVERE = {"storm_severity": 5, "damaged_poles": 12, "crews": 2, "access_ok": False,
          "declared_needs": ["medical_equipment"]}
COORD = re.compile(r"-?\d{2,3}\.\d{5,}")
FORBIDDEN = re.compile(r"STATION\d|CIR\d|premise|latitude|longitude", re.I)


def check_privacy(body: dict):
    s = json.dumps(body)
    assert not COORD.search(s), "coordinate-like value leaked"
    assert not FORBIDDEN.search(s), f"internal field leaked: {FORBIDDEN.search(s).group()}"


def test_all():
    with TestClient(app) as c:
        h = c.get("/health").json()
        print("health:", h)
        assert h["data_mode"] in ("live", "mock")

        locs = c.get("/locations").json()["locations"]
        assert locs, "no locations"
        check_privacy({"l": locs})
        print(f"{len(locs)} locations, first: {locs[0]}")
        with_hist = next(l["location_id"] for l in locs if l["has_storm_history"])
        no_hist = next((l["location_id"] for l in locs if not l["has_storm_history"]), None)

        for lid in filter(None, [with_hist, no_hist]):
            res = {}
            for name, sc in (("mild", MILD), ("severe", SEVERE)):
                t = time.time()
                r = c.post("/assess", json={"location_id": lid, "scenario": sc})
                ms = (time.time() - t) * 1000
                assert r.status_code == 200, r.text
                b = r.json()
                check_privacy(b)
                m, rr = b["model_estimate"], b["model_estimate"]["restoration_range"]
                assert rr["low_hours"] < rr["high_hours"]
                assert b["meta"]["assumptions"] and b["meta"]["limits"]
                res[name] = b
                print(f"{lid} {name:6s} {ms:5.0f}ms risk={m['risk_band']}({m['risk_score']}) "
                      f"conf={m['confidence']} range={rr['low_hours']}-{rr['high_hours']}h "
                      f"alert={m['alert_rule']} channel={b['customer_actions']['channel']} "
                      f"fallback={b['meta']['fallback']}")
                assert ms < 300 or name == "mild", "slow endpoint"
            assert res["mild"]["model_estimate"] != res["severe"]["model_estimate"], "outputs did not change"
            assert (res["mild"]["model_estimate"]["restoration_range"]["high_hours"]
                    < res["severe"]["model_estimate"]["restoration_range"]["high_hours"])

            for mt in ("pre_storm", "outage_confirmed", "estimate_updated", "restoration_complete"):
                for name, sc in (("mild", MILD), ("severe", SEVERE)):
                    r = c.post("/message", json={"location_id": lid, "message_type": mt, "scenario": sc})
                    assert r.status_code == 200, r.text
                    b = r.json()
                    check_privacy(b)
                    assert b["validated"], b["text"]
                    if mt == "outage_confirmed":
                        print(f"  msg {name}: {b['text']}")

        assert c.post("/assess", json={"location_id": "LOC-999"}).status_code == 404
        assert c.post("/assess", json={"location_id": with_hist, "scenario": {"storm_severity": 9}}).status_code == 422
        assert c.post("/assess", json={"location_id": with_hist,
                                       "scenario": {"declared_needs": ["guess_me"]}}).status_code == 422

    # number validator rejects invented numbers
    facts = {"low_hours": 9.9, "high_hours": 22.7}
    assert validate("Estimate 9.9 to 22.7 hours.", facts)
    assert not validate("Estimate 9.9 to 30 hours.", facts)
    print("smoke test passed")


if __name__ == "__main__":
    test_all()
