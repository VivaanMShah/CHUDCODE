import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { LocationSuggestion, UserLocation } from "./types";

// ============================================================================
// The user's chosen area, shared by every screen.
//
//   const { location, openPicker } = useLocation();
//
// - `location` is null until the user picks an area (from GET /locations).
// - The choice is remembered in the browser (localStorage) between visits.
// - `pickerOpen` / `openPicker()` let any screen open the area picker.
// ============================================================================

const STORAGE_KEY = "storm-watch:area";

interface LocationContextValue {
  location: UserLocation | null;
  setFromSuggestion: (s: LocationSuggestion) => void;
  clearLocation: () => void;
  pickerOpen: boolean;
  openPicker: () => void;
  closePicker: () => void;
}

const LocationContext = createContext<LocationContextValue | null>(null);

// ---- Browser storage (wrapped: private windows / blocked storage can throw) ----

function isValidLocation(value: unknown): value is UserLocation {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return typeof v.label === "string" && typeof v.locationId === "string";
}

function loadSaved(): UserLocation | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return isValidLocation(parsed) ? { ...parsed, source: "saved" } : null;
  } catch {
    return null;
  }
}

function persist(location: UserLocation | null) {
  try {
    if (location) localStorage.setItem(STORAGE_KEY, JSON.stringify(location));
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable: the app still works, it just won't remember */
  }
}

// ---- Provider ------------------------------------------------------------------

export function LocationProvider({ children }: { children: ReactNode }) {
  const [location, setLocationState] = useState<UserLocation | null>(loadSaved);
  // First-time visitors with no saved area see the picker right away.
  const [pickerOpen, setPickerOpen] = useState(() => loadSaved() === null);

  const setFromSuggestion = useCallback((s: LocationSuggestion) => {
    const next: UserLocation = { label: s.label, locationId: s.id, source: "search" };
    setLocationState(next);
    persist(next);
    setPickerOpen(false);
  }, []);

  const clearLocation = useCallback(() => {
    setLocationState(null);
    persist(null);
    setPickerOpen(true);
  }, []);

  const openPicker = useCallback(() => setPickerOpen(true), []);
  const closePicker = useCallback(() => {
    // Can't dismiss the picker until an area has been chosen.
    if (location) setPickerOpen(false);
  }, [location]);

  const value = useMemo<LocationContextValue>(
    () => ({ location, setFromSuggestion, clearLocation, pickerOpen, openPicker, closePicker }),
    [location, setFromSuggestion, clearLocation, pickerOpen, openPicker, closePicker],
  );

  return <LocationContext.Provider value={value}>{children}</LocationContext.Provider>;
}

export function useLocation(): LocationContextValue {
  const ctx = useContext(LocationContext);
  if (!ctx) {
    throw new Error("useLocation must be used inside <LocationProvider>");
  }
  return ctx;
}
