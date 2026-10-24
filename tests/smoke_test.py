"""Smoke test: hit every endpoint with a mild and a severe scenario.

Run:  python -m tests.smoke_test      (or: pytest tests/smoke_test.py)
"""
import json
import re
import time

from fastapi.testclient import TestClient

from app.main import app
from app.assistant import validate

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

        # New optional features
        b = c.post("/assess", json={"location_id": with_hist, "scenario": SEVERE}).json()
        m = b["model_estimate"]
        hist = m["restoration_histogram"]
        assert hist and abs(sum(h["share"] for h in hist) - 1) < 0.02, "histogram shares"
        assert m["simulations"] > 0
        assert all(d["scenario_patch"] for d in m["factor_deltas"]), "what-if patches"
        if h["data_mode"] == "live":
            assert b["meta"]["backtest"]["auc"] > 0.5
            assert b["area"]["upstream_chain"], "upstream chain"
            assert b["area"]["upstream_chain"][0]["device_type"] == "circuit breaker"
        assert b["customer_actions"]["send_timing"].startswith("Call 48"), "medical alerts go out earlier"
        # Applying a what-if patch reproduces its range
        d0 = m["factor_deltas"][0]
        b2 = c.post("/assess", json={"location_id": with_hist,
                                     "scenario": {**SEVERE, **d0["scenario_patch"]}}).json()
        assert b2["model_estimate"]["restoration_range"] == d0["new_range"], "patch mismatch"
        # Backup hours change escalation
        short = {**SEVERE, "backup_hours": 2}
        b3 = c.post("/assess", json={"location_id": with_hist, "scenario": short}).json()
        assert "2 hours" in b3["customer_actions"]["escalation"]["trigger"]
        print("new features ok: histogram, patches, chain, backtest, backup hours")

        # ZIP search + live weather
        z = c.get("/zip/24014")
        if h["data_mode"] == "live":
            assert z.status_code == 200, z.text
            zb = z.json()
            check_privacy(zb)
            assert zb["location_id"] == "ZIP-24014"
            w = c.get("/weather/24014").json()
            check_privacy(w)
            assert w["fallback"] or 1 <= w["severity"] <= 5
            assert "lat" not in json.dumps(w).lower() or '"lat"' not in json.dumps(w)
            za = [c.post("/assess", json={"location_id": "ZIP-24014", "scenario": sc}).json()
                  for sc in (MILD, SEVERE)]
            assert za[0]["model_estimate"] != za[1]["model_estimate"], "ZIP outputs did not change"
            check_privacy(za[1])
            print(f"zip ok: {zb['label']} | weather: severity={w['severity']} chance={w['chance_pct']}% "
                  f"alerts={None if w['alerts'] is None else len(w['alerts'])}")
        # Chat: answers come from the estimate, numbers are checked, bad input is rejected
        ch = c.post("/chat", json={"location_id": with_hist, "question": "How long could it last?", "scenario": SEVERE})
        assert ch.status_code == 200, ch.text
        chb = ch.json()
        check_privacy(chb)
        assert chb["validated"] and chb["source"] in ("model", "template") and chb["suggestions"]
        assert c.post("/chat", json={"location_id": with_hist, "question": "x" * 301}).status_code == 422
        assert c.post("/chat", json={"location_id": "LOC-999", "question": "hi"}).status_code == 404
        print(f"chat ok ({chb['source']}): {chb['answer'][:70]}")
        # Phone alerts: config, validation, and clean-up (a fake address can't be pushed to, so no send here)
        cfg = c.get("/alerts/config").json()
        assert cfg["available"] and len(cfg["public_key"]) > 60
        fake = {"endpoint": "https://fcm.googleapis.com/fcm/send/smoke", "keys": {"p256dh": "B" + "A" * 80, "auth": "x" * 22}}
        sub = c.post("/alerts/subscribe", json={"subscription": fake, "zip": "24014"})
        assert sub.status_code == 200, sub.text
        evil = {**fake, "endpoint": "https://evil.example.com/x"}
        assert c.post("/alerts/subscribe", json={"subscription": evil, "zip": "24014"}).status_code == 422
        assert c.post("/alerts/unsubscribe", json={"id": sub.json()["id"]}).json()["ok"]
        assert c.post("/alerts/test", json={"id": sub.json()["id"]}).status_code == 404
        print("alerts ok: config, subscribe, bad-address guard, unsubscribe")

        assert c.get("/zip/99999").status_code == 404
        assert c.get("/zip/12ab5").status_code == 422
        assert c.post("/assess", json={"location_id": "ZIP-99999"}).status_code == 404

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
