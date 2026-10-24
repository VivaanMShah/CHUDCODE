# AI Outage Concierge — Backend

FastAPI backend for the HackOHI/O 2026 AEP challenge. A customer picks an anonymized location and a storm scenario; the API returns outage risk, a restoration time range, prep guidance, alert eligibility, and customer messages.

**Status: live data.** All engines run on the organizer data in `./data/`. The risk model is backtested on the largest storm in the damage records (February 2025): **AUC 0.82 across 104 areas**. If the data files are missing, the app still starts in mock mode and every response sets `meta.fallback = true`.

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Interactive docs: http://localhost:8000/docs

First start builds features from the shapefiles (~5 s) and caches them to `data/_cache/store.pkl`. Later starts load the cache instantly. Delete the cache file to force a rebuild; it also rebuilds on its own when a data file changes.

Smoke test (hits every endpoint with a mild and a severe scenario):
```bash
python -m tests.smoke_test
```

Print the data schema report:
```bash
python -m app.data
```

### Frontend (React + Vite)

The web app lives at the repo root (`src/`, `index.html`). It needs Node.js 18+.

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api/* to the backend on :8000
```

Start the backend first (`uvicorn app.main:app --port 8000`). For a deployed build, point the app at the API:

```bash
VITE_API_URL=https://your-api.example.com npm run build
```

Note: the committed `node_modules/` was built on macOS. On Windows or Linux run `npm install` (or `npm ci`) before `npm run dev`, or Vite won't start.

Docker:
```bash
docker build -t outage-concierge . && docker run -p 8000:8000 outage-concierge
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Health check, shows `data_mode`, loaded files, backtest AUC |
| GET | `/zip/{zip}` | Turn a 5-digit ZIP into an area id (`ZIP-24014`). No coordinates returned |
| GET | `/weather/{zip}` | Live storm strength 1-5, next 12 hours, National Weather Service alerts |
| GET | `/locations` | Anonymized circuit-level areas (`LOC-001`...). The app now uses ZIPs instead |
| POST | `/assess` | Risk, restoration range, actions, alerts |
| POST | `/message` | Customer message for a given stage |

Errors: unknown `location_id` → `404`. Invalid input (for example `storm_severity: 9`, or an unknown `declared_needs` value) → `422` with field details.

## GET /health

```json
{ "status": "ok", "data_mode": "live", "files_loaded": 12, "files_missing": ["indirect_labor", "storms_labor"], "backtest_auc": 0.817 }
```

## GET /locations

```json
{
  "locations": [
    { "location_id": "LOC-001", "label": "Roanoke area 1", "customers_served_band": "1,000 to 2,000", "has_storm_history": true }
  ],
  "data_mode": "live"
}
```

104 locations. Each one is a neighborhood-sized area (one distribution circuit with at least 50 customers). Labels use the city name plus a number, never internal circuit or station codes. `has_storm_history: false` areas return a fallback from `/assess`, which is useful for demoing the fallback state.

## ZIP search and live weather

**ZIP is the only thing a customer enters.** It is saved on their device and never sent anywhere except this API.

- `GET /zip/24014` → `{zip, location_id: "ZIP-24014", label, city, customers_served_band}`. 404 if we have no data for the ZIP, 422 if it isn't 5 digits.
- Use `location_id` (`ZIP-24014`) anywhere `LOC-001` worked: `/assess`, `/message`. A ZIP is served by several circuits, so the engine uses a customer-weighted "typical circuit" for it, and `meta.limits` says so.
- ZIP centers are an offline CSV (`app/zip_centers.csv`, built once by `python -m scripts.build_zip_table`), so a typed ZIP is never sent to a third party. Customers are matched to the nearest ZIP center, so ZIP borders are approximate.
- `GET /weather/24014` → `severity` (1-5), `severity_label`, `chance_pct`, `reasons`, `hourly` (next 12 hours: rain, gusts, wind direction), `alerts` (National Weather Service; `null` = could not check), `fallback`. Only the ZIP's public center point goes to Open-Meteo and NWS. Cached 10 minutes.
- The app uses `severity` as the default `storm_severity` ("live mode"). The "Try a storm" panel overrides it ("simulate mode"), which keeps demos repeatable on a sunny day.

### Weather model card

| | |
|---|---|
| Question | Will today be a *damage day* (the utility logs storm damage)? |
| Model | Logistic regression on 5 features: gust, 2-day max gust, rain today, rain yesterday, 3-day rain |
| Data | 1,425 days (Sep 2022 to Aug 2026), 55 damage days. Weather is from archived forecasts, so training matches what the live forecast looks like |
| Validation | Time-ordered expanding window, 6 folds, 5-day gap, scaler and model fit inside each window |
| Result | Out-of-time AUC **0.73**; Brier 0.044 vs 0.047 for always guessing the base rate. A modest signal |
| Severity | Chance of a damage day: <5% = 1 Calm, 5-12% = 2, 12-30% = 3, 30-55% = 4, 55%+ = 5 |
| Not used | Repair dates, repair status, or any after-the-fact field |
| Caveats | Only 55 positive days and one dominant storm. Reports lag the weather by about a day. We tried a few feature sets on the same folds, so the score is slightly optimistic. Tree models did worse with this little data |

Retrain with `python -m app.train_weather` (needs internet). It writes `app/weather_model.json`.

### Privacy notes

- No names, addresses, coordinates, or customer ids in any response. Customer points are used once at startup to match ZIPs, then dropped.
- The audit log keeps only the first 3 digits of a ZIP (`ZIP-240xx`).
- uvicorn's access log records request paths (which include the ZIP). Turn it off or shorten it in production (`--no-access-log`).
- Radar "Find a hospital" opens Google Maps with the ZIP. It is a link the customer taps, not data we send. Real hospital names, numbers, and locations from the provided data are never returned (hard rule 5).

## Scenario input (shared by /assess and /message)

| Field | Type | Range | Default | Notes |
|---|---|---|---|---|
| `storm_severity` | int | 1–5 | 3 | 1 = minor, 5 = severe. 4 = calibrated to the Feb 2025 storm |
| `damaged_poles` | int | 0–200 | 0 | Known or assumed damage |
| `crews` | int | 1–50 | 2 | Crews assigned |
| `access_ok` | bool | | true | false = roads blocked |
| `declared_needs` | string[] | | [] | `medical_equipment`, `accessibility`, `critical_facility`. Opt-in only, never inferred |
| `backup_hours` | float \| null | 0–168 | null | *(optional, new)* How long the customer's backup power lasts. Used with `medical_equipment` for the alert rule and escalation |

## POST /assess

Request:
```json
{
  "location_id": "LOC-020",
  "scenario": {
    "storm_severity": 4,
    "damaged_poles": 6,
    "crews": 3,
    "access_ok": false,
    "declared_needs": ["medical_equipment"]
  }
}
```

Response (real output, `factor_deltas` and `assumptions` trimmed):
```json
{
  "location_id": "LOC-020",
  "confirmed": [
    {
      "fact": "past_damage_reports",
      "text": "Utility records show 2 storm damage reports in this area across 2 past storms.",
      "source": "ARCOS damage assessment records",
      "value": 2.0
    }
  ],
  "model_estimate": {
    "risk_band": "high",
    "risk_score": 1.0,
    "confidence": "medium",
    "confidence_reason": "Confidence is medium: model backtest AUC 0.82 on the February 2025 storm; 2 past damage reports in this area; restoration range spread 1.4 times the median.",
    "reasons": [
      { "factor": "damaged_poles", "text": "6 damaged poles are assumed in this scenario.", "direction": "increases_risk" },
      { "factor": "storm_severity", "text": "The storm is rated 4 out of 5.", "direction": "increases_risk" },
      { "factor": "primary_miles", "text": "This area has about 50 miles of overhead power lines, more than most areas.", "direction": "increases_risk" }
    ],
    "restoration_range": { "low_hours": 33.5, "high_hours": 129.0, "median_hours": 67.5, "method": "monte_carlo_p10_p90" },
    "factor_deltas": [
      {
        "change": "+1 crew",
        "new_range": { "low_hours": 26.6, "high_hours": 95.7, "median_hours": 51.4, "method": "monte_carlo_p10_p90" },
        "delta_text": "about 16.1 hours faster"
      }
    ],
    "alert_eligible": true,
    "alert_rule": "R2: declared medical equipment need and outage risk"
  },
  "customer_actions": {
    "actions": [
      "Confirm backup power for medical equipment, or plan where you will go if it runs out.",
      "Charge phones and power banks.",
      "Keep flashlights and batteries where you can find them."
    ],
    "safety_messages": [
      "Stay at least 30 feet from downed power lines and report them right away.",
      "Never run a generator indoors or in a garage."
    ],
    "channel": "phone_call",
    "send_timing": "Call 24 hours before the storm arrives",
    "escalation": {
      "trigger": "Outage expected to last longer than backup power (about 8 hours)",
      "action": "Flag for a utility follow-up call"
    }
  },
  "meta": {
    "fallback": false,
    "fallback_reason": null,
    "assumptions": [
      "Risk model: logistic regression on miles of overhead line, customers served, and past damage. Trained on history before the February 2025 storm and backtested on it (AUC 0.82 across 104 areas).",
      "Labor hours per repair are placeholders until the STORMS file is provided: pole 6.0, primary wire 2.5, transformer 3.0, fuse or switch 1.0 crew-hours."
    ],
    "limits": [
      "Estimates are model output, not confirmed restoration times from the utility.",
      "The range covers the 10th to 90th percentile of 2000 simulations; actual times can fall outside it."
    ],
    "data_mode": "live",
    "request_id": "a02ee97c0d4e"
  },
  "area": {
    "label": "Blue Ridge area 1",
    "customers_served_band": "1,000 to 2,000",
    "upstream_device_type": "circuit breaker",
    "customers_on_upstream_device_band": "1,000 to 2,000",
    "critical_facility_nearby": true
  }
}
```

### Response sections

- **`confirmed`** — facts from utility records only (ARCOS damage history for this area: past damage reports, reports from the Feb 2025 storm, reports not yet marked repaired). Each item has `fact`, `text`, `source`, `value`. Empty when nothing is on record. Show these separately from estimates.
- **`model_estimate`** — everything the model predicts. Always label it as an estimate in the UI.
  - `risk_band`: `low` | `medium` | `high`; `risk_score` 0–1
  - `confidence`: `low` | `medium` | `high`, with `confidence_reason`. Lowest of three real signals: model backtest AUC, this area's damage history volume, and Monte Carlo spread.
  - `reasons`: top 3 factors, each with plain-English `text` and `direction`
  - `restoration_range`: always a range (`low_hours`–`high_hours`), never one number. `method` is `monte_carlo_p10_p90`
  - `factor_deltas`: what-if changes: `+1 crew`, `+2 crews`, `roads cleared` / `roads blocked`, `half as many damaged poles`, `storm one level weaker`
  - `alert_eligible` + `alert_rule`: the rule that decided it (R0–R3)
- **`customer_actions`** — `actions`, `safety_messages`, `channel` (`sms` | `push` | `email` | `phone_call`), `send_timing`, `escalation`
- **`meta`** — `fallback` (true = show a "we can't predict this reliably" state), `fallback_reason`, `assumptions`, `limits`, `data_mode`, `request_id`
- **`area`** *(optional)* — coarse context: area label, customer-count bands, upstream protective device type, `critical_facility_nearby` (yes/no only, never which facility or where), and `upstream_chain`: the typical device chain from the substation breaker down to this area, each step with `device_type` and an approximate `customers_approx` (rounded; no device IDs)

**Optional fields added later** (safe to ignore):
- `model_estimate.restoration_histogram`: list of `{low_hours, high_hours, share}` buckets of the simulated outcomes, plus `model_estimate.simulations` (count)
- `factor_deltas[].scenario_patch`: scenario fields that reproduce that what-if, e.g. `{"crews": 4}`. Apply it to the sliders to "try" the change
- `meta.backtest`: `{event_label, areas, auc, high_band_hit_rate, low_band_hit_rate, high_band_areas, low_band_areas}`. Held-out check on the Feb 2025 storm: of 52 areas rated high, 81% had damage; of 21 rated low, 19% did
- Medical-equipment customers get alerts 48 hours ahead (24 hours earlier than the standard high-risk alert), by phone

## POST /message

Request:
```json
{ "location_id": "LOC-020", "message_type": "outage_confirmed", "scenario": { "storm_severity": 4, "damaged_poles": 6, "crews": 3 } }
```

`message_type`: `pre_storm` | `outage_confirmed` | `estimate_updated` | `restoration_complete`

Response:
```json
{
  "location_id": "LOC-020",
  "message_type": "outage_confirmed",
  "text": "We've confirmed an outage in your area. Our current estimate is about 1 to 4 days. We'll update you if this changes.",
  "channel": "sms",
  "send_timing": "Send as soon as the outage is confirmed",
  "validated": true,
  "meta": { "...": "same shape as /assess meta" }
}
```

Ranges over 48 hours are written in days ("about 4 to 17 days"). When `meta.fallback` is true, the message skips numbers and says a reliable estimate is coming.

`validated` is true when every number in `text` matches the structured data. The LLM only rewords; it never produces numbers. LLM polish is off by default. Set `LLM_POLISH=bedrock` (with `boto3` installed and AWS credentials) to turn it on; any LLM output with an unmatched number is thrown away and the template is used.

## How it works

| Module | What it does |
|---|---|
| `app/data.py` | Loads all shapefiles + topology at startup, checks required columns, builds one feature row per circuit, caches to disk. Missing files → that feature falls back, app still starts. |
| `app/resolver.py` | Location ID → circuit features, upstream device, damage history |
| `app/risk.py` | Logistic regression: miles of overhead line, customers served, past damage. Trained on history *before* the Feb 2025 storm, 5-fold CV backtest on that storm (AUC 0.82, Brier 0.17). Severity shifts the log-odds; assumed damaged poles push risk toward certain. |
| `app/restoration.py` | Monte Carlo, 2000 draws, fixed seed: damage counts (Poisson), labor-hour noise, travel delay, blocked roads, crew availability, repair-queue position, 16-hour shifts → p10–p90. Counterfactuals reuse the same random draws so only the changed input moves the range. ~5 ms. |
| `app/rules.py` | Prep tips by risk band and duration, declared-need modifiers, alert rules R0–R3, channel, timing, escalation |
| `app/messages.py` | Templates for 4 message types, optional LLM polish, number validator |
| `app/safety.py` | Strips coordinates, internal codes, and sensitive keys from every response; JSON-lines audit log in `logs/audit.log` (declared needs logged as a count only) |
| `app/engine.py` | Ties it together: confidence, fallback, confirmed facts, assumptions, limits |

### Data notes

- Customer → circuit is joined through the transformer (`aep_xfmr_l` → transformer `location_n` → `aep_statio` + `aep_circui`). The raw customer `circuit_id` is not 1:1 with ARCOS circuit names.
- Topology file: 117,595 premises matched, 99.97% of customers. Upstream device = nearest recloser, else the circuit breaker.
- Poles have no circuit field; each company-owned pole is matched to the nearest primary line within 30 m.
- ARCOS dates are day-level only. Storm events are grouped by gaps of more than 3 days. The largest is Feb 12–18, 2025 (1,254 records).
- Not loaded on purpose: customer coordinates, addresses, premise numbers, and the customer life-support flag (declared needs are opt-in only). Facility names and locations are reduced to a yes/no flag per area.
- **Missing from the package:** STORMS labor-hour and indirect-labor Excel files. Restoration uses placeholders from `app/config.py`, listed in every response's `meta.assumptions`.

## Frontend notes

React app in `src/`, wired to this API:

| Screen | What it does |
|---|---|
| ZIP picker | Types a ZIP, `GET /zip/{zip}` on the 5th digit |
| Home | One hero card: live weather, outage risk, restoration range with histogram, what-if chips. "Try a storm" panel for the simulator. Collapsible details: why, upstream chain, utility records, About |
| Radar | Forecast illustration drawn from the hourly forecast (not real radar imagery): play and scrub 12 hours, Rain and Wind layers, tap a spot for conditions, and a "Need help?" panel (911, the power company, find a hospital, find a shelter) |
| Alerts | Real NWS alerts, the outage alert rule and escalation, and the 4-stage message timeline with "Verified numbers" |
| Prepare | Checklist, safety messages, downed-line guidance (demo, no reporting) |
| Chat | Sample replies only. Reserved for the Bedrock chatbot |

- API layer: `src/api/backend.ts`, types in `src/api/contract.ts`, contact numbers in `src/config.ts` (the power company line is the Appalachian Power Virginia outage number).
- The last estimate is saved on the device and shown with an offline note if the backend is unreachable.

## Project layout

```
app/
  main.py         routes, CORS
  models.py       pydantic contract (source of truth for the JSON shape)
  config.py       paths, Monte Carlo settings, labeled placeholders
  data.py         loader, schema checks, feature build, cache, mock store
  resolver.py     location -> circuit features
  risk.py         risk model + backtest
  restoration.py  Monte Carlo range + counterfactuals
  rules.py        actions, alerts, channel, escalation
  messages.py     templates, LLM polish hook, number validator
  safety.py       response scrubbing, audit log
  engine.py       request orchestration
tests/
  smoke_test.py   mild vs severe on every endpoint, plus the newer optional fields
app/weather.py, train_weather.py, zip_centers.csv, weather_model.json   live weather model
scripts/          one-off builders (ZIP table)
src/              React + Vite frontend
  api/            backend client, contract types, sample data
  assessment.tsx  shared scenario + /assess state (with offline copy)
  location.tsx    chosen area
  screens/        Home, Alerts (timeline), Storm (Prepare), Radar, Chat
```

## Roadmap

Done: ZIP search, weather-driven storm strength, calmer UI, interactive forecast radar with help actions, NWS alerts.

Next:
1. **Bedrock chatbot** (`POST /chat`): the model only rewords text from the `/assess` JSON, and the number validator checks every number. Use the Bedrock Converse API with a model id from an env var.
2. **AWS hosting:** the API needs `data/` at runtime (about 500 MB, gitignored), so bake it into the container image or mount it from S3. Set `VITE_API_URL` for the frontend build, and tighten CORS from `*` to the frontend's origin.
3. Load the STORMS and indirect-labor files when available (replace placeholders in `app/config.py`).

## Built during HackOHI/O 2026

All code in this repo was written during the event. Libraries: FastAPI, pydantic, uvicorn, pandas, geopandas, pyogrio, shapely, scikit-learn, scipy, networkx. Data: organizer-provided AEP challenge package.
