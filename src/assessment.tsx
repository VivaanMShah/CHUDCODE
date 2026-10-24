import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ApiError, assess } from "./api/backend";
import type { AssessResponse, Scenario } from "./api/contract";
import { useLocation } from "./location";

// ============================================================================
// Storm scenario + the backend's assessment for the chosen area.
//
//   const { scenario, updateScenario, data, loading, error } = useAssessment();
//
// Any change to the area or the scenario re-calls POST /assess (debounced),
// so every screen shows the same, current estimate.
// ============================================================================

const STORAGE_KEY = "storm-watch:scenario";
const DEBOUNCE_MS = 180;

export const DEFAULT_SCENARIO: Scenario = {
  storm_severity: 3,
  damaged_poles: 0,
  crews: 2,
  access_ok: true,
  declared_needs: [], // opt-in only, never inferred
};

interface AssessmentContextValue {
  scenario: Scenario;
  updateScenario: (patch: Partial<Scenario>) => void;
  resetScenario: () => void;
  data: AssessResponse | null; // last good result (kept while a new one loads)
  loading: boolean;
  error: string | null;
  retry: () => void;
}

const AssessmentContext = createContext<AssessmentContextValue | null>(null);

function loadScenario(): Scenario {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_SCENARIO;
    const s = JSON.parse(raw) as Partial<Scenario>;
    return {
      storm_severity: clamp(s.storm_severity, 1, 5, DEFAULT_SCENARIO.storm_severity),
      damaged_poles: clamp(s.damaged_poles, 0, 200, DEFAULT_SCENARIO.damaged_poles),
      crews: clamp(s.crews, 1, 50, DEFAULT_SCENARIO.crews),
      access_ok: typeof s.access_ok === "boolean" ? s.access_ok : true,
      declared_needs: Array.isArray(s.declared_needs)
        ? s.declared_needs.filter((n) =>
            ["medical_equipment", "accessibility", "critical_facility"].includes(n),
          )
        : [],
    };
  } catch {
    return DEFAULT_SCENARIO;
  }
}

function clamp(v: unknown, lo: number, hi: number, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? Math.min(hi, Math.max(lo, Math.round(v))) : fallback;
}

export function AssessmentProvider({ children }: { children: ReactNode }) {
  const { location, clearLocation } = useLocation();
  const locationId = location?.locationId;

  const [scenario, setScenario] = useState<Scenario>(loadScenario);
  const [data, setData] = useState<AssessResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(scenario));
    } catch {
      /* not critical */
    }
  }, [scenario]);

  // Drop the old area's result when the area changes.
  useEffect(() => {
    setData(null);
    setError(null);
  }, [locationId]);

  useEffect(() => {
    if (!locationId) return;
    const ctrl = new AbortController();
    setLoading(true);
    const timer = setTimeout(() => {
      assess(locationId, scenario, ctrl.signal)
        .then((d) => {
          setData(d);
          setError(null);
        })
        .catch((e: unknown) => {
          if (ctrl.signal.aborted) return;
          if (e instanceof ApiError && e.status === 404) {
            clearLocation(); // saved area no longer exists on the server
            return;
          }
          setError("Couldn't reach the outage service. Check that the backend is running.");
        })
        .finally(() => {
          if (!ctrl.signal.aborted) setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [locationId, scenario, attempt, clearLocation]);

  const updateScenario = useCallback(
    (patch: Partial<Scenario>) => setScenario((s) => ({ ...s, ...patch })),
    [],
  );
  const resetScenario = useCallback(() => setScenario(DEFAULT_SCENARIO), []);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  const value = useMemo<AssessmentContextValue>(
    () => ({ scenario, updateScenario, resetScenario, data, loading, error, retry }),
    [scenario, updateScenario, resetScenario, data, loading, error, retry],
  );

  return <AssessmentContext.Provider value={value}>{children}</AssessmentContext.Provider>;
}

export function useAssessment(): AssessmentContextValue {
  const ctx = useContext(AssessmentContext);
  if (!ctx) throw new Error("useAssessment must be used inside <AssessmentProvider>");
  return ctx;
}

// ---- Display helpers shared by screens ----------------------------------------

const BAND_LABEL = { low: "Low", medium: "Medium", high: "High" } as const;
export const bandLabel = (b: keyof typeof BAND_LABEL) => BAND_LABEL[b];

// "33–129 hours" or, for long outages, "1.4–5.4 days"
export function formatRange(lo: number, hi: number): string {
  if (hi >= 48) return `${fmt(lo / 24)}–${fmt(hi / 24)} days`;
  return `${fmt(lo)}–${fmt(hi)} hours`;
}

export function formatHours(h: number): string {
  if (h >= 48) return `about ${fmt(h / 24)} days`;
  return `about ${fmt(h)} hours`;
}

function fmt(x: number): string {
  return x >= 10 ? String(Math.round(x)) : String(Math.round(x * 10) / 10);
}

export const CHANNEL_LABEL: Record<string, string> = {
  sms: "Text message",
  push: "App notification",
  email: "Email",
  phone_call: "Phone call",
};
