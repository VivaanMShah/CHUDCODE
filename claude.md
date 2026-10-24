# CLAUDE.md — AI Outage Concierge (HackOHI/O 2026, AEP challenge)

Backend for a Flutter mobile app that helps utility customers during a storm. A customer picks an anonymized location and a storm scenario. The API returns outage risk, a restoration time range, prep guidance, alert eligibility, and a customer message.

This is a 22-hour hackathon project. A focused, explainable prototype beats broad features. Judges must be able to change a scenario input and see the output change.

## Current state

- All 7 modules are built and running on real data (`data_mode: "live"`). Mock mode (`data.mock_store`) kicks in only if the data files are missing.
- `app/models.py` is the API contract. A teammate is building the Flutter app against it right now.
- The app is 5 files: `data.py` (settings, loader, area lookup), `engines.py` (risk, restoration, rules, live weather, orchestration), `assistant.py` (Bedrock, messages, chat), `main.py` (routes + safety layer), `models.py` (contract). `deploy.py` hosts it on AWS.
- Missing from the data package: STORMS labor-hour and indirect-labor Excel files. Restoration uses labeled placeholders at the top of `app/data.py` until they arrive.
- Smoke test: `python -m tests.smoke_test`.
- Frontend is a React + Vite web app in `src/` (not Flutter), wired to the API through `src/api/backend.ts`. Radar and Chat screens are labeled sample data.

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload      # docs at http://localhost:8000/docs
```

Deployed on AWS by the user, served over HTTPS. Don't change deploy config unless asked.

## Hard rules (never break these)

1. **Don't break the contract.** Never rename, remove, or change the type of a field in `app/models.py`. Adding optional fields is OK. If a change is truly needed, stop and tell me first so I can warn the frontend.
2. **Ranges, never single numbers.** Restoration is always `low_hours` to `high_hours`.
3. **Label model output vs confirmed facts.** Only real utility data goes in `confirmed`. Everything predicted goes in `model_estimate`.
4. **The LLM never produces numbers.** It only rewords text built from structured JSON. Every number in an LLM output must be checked against the input JSON before returning; if any doesn't match, fall back to the template text.
5. **Privacy.** Never return coordinates, precise sensitive-facility locations, personal data, employee info, or internal-only operational fields. A coarse "critical facility nearby: yes/no" is the most detail allowed.
6. **Declared needs are opt-in only.** Never infer medical, accessibility, or critical-facility status.
7. **Always have a fallback.** When confidence is low or data is missing, set `meta.fallback = true` with a clear `fallback_reason`.
8. **Confidence must come from something real**: history volume, model spread, or a backtest. Never a hardcoded value once real data is in.
9. **Every response includes `assumptions` and `limits`** in `meta`. Update them whenever logic changes.

## API

- `GET /health`
- `GET /locations` — anonymized selectable locations
- `POST /assess` — `location_id` + scenario (`storm_severity` 1-5, `damaged_poles`, `crews`, `access_ok`, `declared_needs[]`). Response sections: `confirmed`, `model_estimate`, `customer_actions`, `meta`.
- `POST /message` — `location_id`, `message_type` (`pre_storm`, `outage_confirmed`, `estimate_updated`, `restoration_complete`), scenario.
- Skip `/chat` unless everything else works.

Full contract with examples is in `README.md`.

## Stack

Python, FastAPI, pydantic v2, pandas, geopandas, numpy, networkx, scikit-learn or LightGBM. LLM API for wording only. Add new dependencies to `requirements.txt` with pinned versions.

## Data (organizer-provided, sanitized)

Put files in `./data/` (gitignored). Inspect real schemas before writing code against them; column names in the brief may not match.

| Source                      | Format    | Use                                                                                                                                     |
| --------------------------- | --------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| ARCOS damage assessment     | shapefile | Incident, region, circuit, substation, asset type, damage type, quantity, repair status/date, masked coords. Risk features and history. |
| Circuit GIS extracts        | shapefile | Circuits, protective devices, relationships                                                                                             |
| Upline flow / topology      | TXT       | Premise to upstream equipment. networkx graph.                                                                                          |
| STORMS labor-hour reference | Excel     | Labor hours by damage type                                                                                                              |
| Indirect labor assumptions  | Excel     | Travel and nonproductive multipliers                                                                                                    |
| Priority locations          | shapefile | Hospitals, fire, ambulance. Never expose exact locations.                                                                               |
| Structure diagrams          | PDF       | Low priority, ignore unless asked                                                                                                       |

When inspecting data, report: columns, dtypes, row counts, null rates, join keys between files, and what's usable vs missing. Don't guess at a schema.

## Modules to build (in this order)

1. **Data loader** (`app/data.py`): load and cache all files at startup. Report schema problems clearly. App must still start if a file is missing; dependent features fall back.
2. **ID resolver** (`app/data.py`): location ID → circuit, upstream device, nearby past damage.
3. **Risk engine** (`app/engines.py`): band low/medium/high, real confidence, top 3 plain-English reasons. Logistic regression or LightGBM; weighted score if data is sparse.
4. **Restoration engine** (`app/engines.py`): labor hours × damage qty × travel mult × nonproductive mult ÷ crews. Monte Carlo over damage counts, travel delays, crew availability → 10th to 90th percentile range. Counterfactual deltas ("+2 crews → X to Y hours"). Recompute on damage, crews, or access changes.
5. **Rules engine** (`app/engines.py`): prep tips and safety messages by risk band and duration, declared-need modifiers, alert eligibility with the rule named, channel, timing, escalation trigger.
6. **Message generator** (`app/assistant.py`): templates for the 4 message types. Optional LLM polish from structured JSON, then the number validator.
7. **Safety layer** (`app/main.py`): strip coords and sensitive fields from every response, input validation, fallback flag, audit log.

When a real module replaces a mock, switch `data_mode` from `"mock"` to `"live"` for that path.

## Unique features (after core works)

1. Monte Carlo restoration range
2. Counterfactual explanations in `factor_deltas`
3. Message number validator
4. Stretch only if data allows: blast radius / step restoration with networkx, backtested confidence on held-out events, storm replay mode, critical-facility-nearby flag

## How to work

- Keep endpoints fast (under ~300 ms). Load and precompute at startup, not per request. Fix Monte Carlo sample count and seed so the demo is repeatable.
- Plain-English text for customers: short sentences, no jargon, no internal codes.
- After each change, run the smoke test: hit every endpoint with a mild and a severe scenario and confirm outputs differ and nothing errors.
- Commit after every working step. Never leave `main` broken.
- If something will take more than ~30 minutes, say so and suggest a cheaper version.
- Ask before deleting files or rewriting working modules.

## Schedule

- Feature freeze at **hour 18**. After that, bug fixes only. If I ask for a new feature after hour 18, remind me.
- Cut order if behind: stretch features first. **Never cut** the fallback, the restoration range, or scenario inputs.
- Last hours: record a 3-minute demo video, update README.
