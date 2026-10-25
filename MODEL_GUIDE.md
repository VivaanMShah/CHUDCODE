# Editing the outage model

Start with `app/engines.py` → `assess()`. It reads the location, calculates risk,
simulates restoration, checks confidence, and applies the customer rules.
You can change each part independently without touching the API routes or frontend.

## Where to edit

| What you want to change | File | Start here |
| --- | --- | --- |
| Low / medium / high risk boundaries | `app/risk.py` | `LOW_BELOW`, `MEDIUM_BELOW` |
| Inputs to the area-exposure model | `app/risk.py` | `RISK_FEATURES`, `_model_inputs()`, `area_score()` |
| Expected damage at each storm strength | `app/restoration.py` | `PATH_ITEMS` (five entries, strengths 1–5) |
| Restoration calculation / what-if scenarios | `app/restoration.py` | `Draws.hours_until_power_returns()`, `estimate_restoration()` |
| Tips, alerts, channel, timing | `app/rules.py` | `tips_and_safety()`, `alert_decision()`, `channel_and_timing()` |
| Weather features or storm arrival | `app/weather.py` | `make_features()`, `ARRIVAL_GUST_MPH`, `ARRIVAL_RAIN_IN` |
| Weather training and evaluation | `app/train_weather.py` | Settings at the top, `train_weather()` |
| Simulation count / random seed / default backup hours | `app/data.py` | `MC_SAMPLES`, `MC_SEED`, `MEDICAL_BACKUP_HOURS` |
| Confidence, fallback, displayed assumptions | `app/engines.py` | `_confidence()`, `_fallback()`, `_assumptions()`, `_limits()` |
| Chat and customer message wording | `app/assistant.py` | Templates and number validation |
| Accepted scenario fields and defaults | `app/models.py` | `Scenario` (this file defines API shapes, not prediction logic) |

## Try one edit

To classify chances below 2% as low and below 6% as medium, edit `app/risk.py`:

```python
LOW_BELOW, MEDIUM_BELOW = 0.02, 0.06
```

These are example thresholds, not validated improvements. Restart the backend,
try mild and severe scenarios, and check that the explanations still make sense.
The separate `RISK_BANDS` setting in `data.py` is for the exposure model's backtest;
it does not control the customer-facing bands.

You can call the restoration model directly without running a server:

```python
from app.models import Scenario
from app.restoration import estimate_restoration
from app.risk import FALLBACK_ANALOG

scenario = Scenario(storm_severity=4, damaged_poles=6)
result = estimate_restoration(FALLBACK_ANALOG, scenario)
print(result["range"])
print(result["deltas"])
```

`FALLBACK_ANALOG` contains illustrative placeholder data. The running app uses
the trained history tables from `app/weather_model.json` when available.

## How the calculations work

- **Risk:** random forest ranks an area's exposure using line miles,
  customer count, and damage history. Historical damage rates for similar areas
  and storm strengths become the customer risk estimate.
- **Restoration:** draw historical repair times for simulated damaged items.
  Each simulation ends when its slowest repair finishes. Show the 10th–90th
  percentile range, with the same random draws reused for what-if comparisons.
- **Weather:** gust and rain features feed a saved logistic regression model.
  Its result selects storm strength. Training and live forecasts share
  `make_features()` so their calculations stay aligned.
- **Advice:** plain rules use risk, duration, and the customer's declared needs.

Current limitation: `crews` and `access_ok` are accepted by the API but do not
affect this empirical restoration model. Damaged poles are capped at 20 in the
simulation. Adding crew/access effects is a separate modeling change that needs
explicit assumptions and tests. This refactor preserves the existing estimates.

## Check a change

From the repository root, with the Python environment activated:

```bash
python -m unittest tests.test_model
python -m tests.smoke_test
```

The unit tests run offline with illustrative data. The smoke test exercises the
API; live data, weather, and optional service configuration affect that run.
On Windows, use `.venv\Scripts\python.exe` if your environment is not activated.

Retrain after changing weather features or training settings:

```bash
python -m app.train_weather
```

This needs the organizer data and internet and overwrites `app/weather_model.json`.
The original `python -m app.engines train-weather` command still works.
Changing area-model inputs requires updating both training and prediction input
builders plus their explanations; the area model trains at backend startup.

Keep restoration ranges, declared needs, missing-data fallback, and number
validation intact. If you change a formula or assumption, also update the text
in `engines.py` so customers see what the model actually does. Preserve the
existing response fields in `models.py` so the frontend keeps working.

## Logistic regression vs. random forest

`app/risk.py` now defaults to `RISK_MODEL = "random_forest"`. Its editable settings
are `FOREST_TREES = 200`, `FOREST_MAX_DEPTH = 4`, and `FOREST_MIN_LEAF = 4`.
The small depth and minimum leaf size limit how closely it can fit this small dataset.

Recreate the comparison and graphs:

```bash
python -m app.compare_risk
python scripts/plot_risk_training.py
```

Plotting requires `pip install -r requirements-analysis.txt`. Results are saved in
`output/training/`. Learning curves show training and validation AUC as the number
of training areas grows; they are not epoch/loss curves. The ROC curves and Brier
scores use out-of-fold predictions on identical splits. Missing-value imputation
is fitted inside each fold, as is scaling for logistic regression.

On 104 areas from the February 2025 storm, logistic regression scored AUC 0.816
and Brier 0.1674; random forest scored AUC 0.835 and Brier 0.1595. Higher AUC and
lower Brier are better. These results hold out areas within one storm; they do
not measure generalization to a separate future storm. Parameters were fixed
for this comparison, not selected through a tuning search.

After changing `RISK_MODEL`, its inputs, or forest settings, rebuild the historical
lookup tables as well, then restart the backend:

```bash
python -c "from app.train_weather import refresh_analog; refresh_analog()"
```

This reuses the saved weather model and refreshes only its area-risk tables.
The first run downloads historical public weather; subsequent runs use a local
cache. Weather forecasting itself remains logistic regression. Random forest's
customer explanations compare its prediction with one input replaced by the
training median; these comparisons are sensitivity checks, not causal effects.
