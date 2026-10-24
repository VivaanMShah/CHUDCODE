import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ApiError, assess, getWeather } from "./api/backend";
import type { AssessResponse, Scenario, WeatherResponse } from "./api/contract";
import { useLocation } from "./location";

// ============================================================================
// What every screen shares: live weather for the ZIP, the storm scenario, and the
// backend's estimate for that scenario.
//
//   const { scenario, live, useLive, setUseLive, updateScenario, data } = useAssessment();
//
// Live mode (default): storm strength comes from the weather forecast.
// Simulate mode: the customer (or a judge) sets storm strength by hand.
// Poles, crews, roads, and needs are always adjustable. Any change re-calls /assess.
// ============================================================================

const SCENARIO_KEY = "storm-watch:scenario";
const LAST_KEY = "storm-watch:last-estimate";
const DEBOUNCE_MS = 300;
const WEATHER_REFRESH_MS = 10 * 60 * 1000;

export const SEVERITY_LABEL = ["", "Calm", "Breezy or wet", "Stormy", "Damaging storm", "Severe storm"];

export const DEFAULT_SCENARIO: Scenario = {
  storm_severity: 3,
  damaged_poles: 0,
  crews: 2,
  access_ok: true,
  declared_needs: [], // opt-in only, never inferred
  backup_hours: null,
};

interface AssessmentContextValue {
  scenario: Scenario; // what is sent to the backend (live severity already applied)
  updateScenario: (patch: Partial<Scenario>) => void;
  resetScenario: () => void;
  live: WeatherResponse | null;
  useLive: boolean; // true = storm strength from live weather
  setUseLive: (on: boolean) => void;
  data: AssessResponse | null; // last good estimate (kept while a new one loads)
  savedAt: string | null; // set when showing a saved copy because the backend is unreachable
  loading: boolean;
  error: string | null;
  retry: () => void;
}

const AssessmentContext = createContext<AssessmentContextValue | null>(null);

// ---- Saved on this device only ------------------------------------------------

function clamp(v: unknown, lo: number, hi: number, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? Math.min(hi, Math.max(lo, Math.round(v))) : fallback;
}

function loadScenario(): Scenario {
  try {
    const s = JSON.parse(localStorage.getItem(SCENARIO_KEY) ?? "{}") as Partial<Scenario>;
    const needs = ["medical_equipment", "accessibility", "critical_facility"];
    return {
      storm_severity: clamp(s.storm_severity, 1, 5, 3),
      damaged_poles: clamp(s.damaged_poles, 0, 200, 0),
      crews: clamp(s.crews, 1, 50, 2),
      access_ok: typeof s.access_ok === "boolean" ? s.access_ok : true,
      declared_needs: Array.isArray(s.declared_needs) ? s.declared_needs.filter((n) => needs.includes(n)) : [],
      backup_hours: typeof s.backup_hours === "number" ? clamp(s.backup_hours, 0, 168, 8) : null,
    };
  } catch {
    return DEFAULT_SCENARIO;
  }
}

function loadLastEstimate(locationId: string): { savedAt: string; data: AssessResponse } | null {
  try {
    const v = JSON.parse(localStorage.getItem(LAST_KEY) ?? "null");
    return v && v.locationId === locationId && v.data ? v : null;
  } catch {
    return null;
  }
}

function saveLastEstimate(locationId: string, data: AssessResponse) {
  try {
    const savedAt = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    localStorage.setItem(LAST_KEY, JSON.stringify({ locationId, savedAt, data }));
  } catch {
    /* storage full or blocked: not critical */
  }
}

// ---- Provider ------------------------------------------------------------------

export function AssessmentProvider({ children }: { children: ReactNode }) {
  const { location, clearLocation } = useLocation();
  const locationId = location?.locationId;
  const zip = location?.zip;

  const [base, setBase] = useState<Scenario>(loadScenario);
  const [useLive, setUseLive] = useState(true);
  const [live, setLive] = useState<WeatherResponse | null>(null);
  const [data, setData] = useState<AssessResponse | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  // Live severity applies only in live mode and only when the weather model could score it.
  const liveSeverity = useLive ? live?.severity ?? null : null;
  const scenario = useMemo<Scenario>(
    () => (liveSeverity ? { ...base, storm_severity: liveSeverity } : base),
    [base, liveSeverity],
  );

  useEffect(() => {
    try {
      localStorage.setItem(SCENARIO_KEY, JSON.stringify(base));
    } catch {
      /* not critical */
    }
  }, [base]);

  // Live weather: on ZIP change, then every 10 minutes.
  useEffect(() => {
    setLive(null);
    if (!zip) return;
    const ctrl = new AbortController();
    const load = () =>
      getWeather(zip, ctrl.signal)
        .then(setLive)
        .catch(() => {
          /* keep the last reading; the app falls back to manual storm strength */
        });
    load();
    const timer = setInterval(load, WEATHER_REFRESH_MS);
    return () => {
      ctrl.abort();
      clearInterval(timer);
    };
  }, [zip]);

  // New ZIP: drop the old estimate.
  useEffect(() => {
    setData(null);
    setSavedAt(null);
    setError(null);
  }, [locationId]);

  // Estimate: re-call /assess whenever the ZIP or scenario changes (debounced).
  useEffect(() => {
    if (!locationId) return;
    const ctrl = new AbortController();
    setLoading(true);
    const timer = setTimeout(() => {
      assess(locationId, scenario, ctrl.signal)
        .then((d) => {
          setData(d);
          setSavedAt(null);
          setError(null);
          saveLastEstimate(locationId, d);
        })
        .catch((e: unknown) => {
          if (ctrl.signal.aborted) return;
          if (e instanceof ApiError && e.status === 404) {
            clearLocation(); // this ZIP is no longer served
            return;
          }
          setError("Couldn't reach the outage service. Check your connection.");
          const saved = loadLastEstimate(locationId); // offline: show the last estimate we saved
          if (saved) {
            setData((cur) => cur ?? saved.data);
            setSavedAt(saved.savedAt);
          }
        })
        .finally(() => !ctrl.signal.aborted && setLoading(false));
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [locationId, scenario, attempt, clearLocation]);

  const updateScenario = useCallback((patch: Partial<Scenario>) => {
    if (patch.storm_severity !== undefined) setUseLive(false); // choosing a strength = simulating
    setBase((s) => ({ ...s, ...patch }));
  }, []);
  const resetScenario = useCallback(() => {
    // Keep the person's declared needs: they come from About you, not the simulator.
    setBase((b) => ({ ...DEFAULT_SCENARIO, declared_needs: b.declared_needs, backup_hours: b.backup_hours }));
    setUseLive(true);
  }, []);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const value = useMemo<AssessmentContextValue>(
    () => ({ scenario, updateScenario, resetScenario, live, useLive, setUseLive, data, savedAt, loading, error, retry }),
    [scenario, updateScenario, resetScenario, live, useLive, data, savedAt, loading, error, retry],
  );
  return <AssessmentContext.Provider value={value}>{children}</AssessmentContext.Provider>;
}

export function useAssessment(): AssessmentContextValue {
  const ctx = useContext(AssessmentContext);
  if (!ctx) throw new Error("useAssessment must be used inside <AssessmentProvider>");
  return ctx;
}

// ---- Display helpers shared by screens ----------------------------------------

export const bandLabel = (b: "low" | "medium" | "high") => ({ low: "Low", medium: "Medium", high: "High" })[b];

function fmt(x: number): string {
  return x >= 10 ? String(Math.round(x)) : String(Math.round(x * 10) / 10);
}

// "33–129 hours" or, for long outages, "1.4–5.4 days"
export function formatRange(lo: number, hi: number): string {
  return hi >= 48 ? `${fmt(lo / 24)}–${fmt(hi / 24)} days` : `${fmt(lo)}–${fmt(hi)} hours`;
}

export function formatHours(h: number): string {
  return h >= 48 ? `about ${fmt(h / 24)} days` : `about ${fmt(h)} hours`;
}

// "R2: declared medical equipment ..." -> "declared medical equipment ..."
export const plainRule = (rule: string) => rule.replace(/^R\d+:\s*/, "");

export const CHANNEL_LABEL: Record<string, string> = {
  sms: "Text message",
  push: "App notification",
  email: "Email",
  phone_call: "Phone call",
};
