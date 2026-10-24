import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  reverseGeocode,
  saveUserLocation,
  type Coords,
} from "api/placeholders";
import type { LocationSuggestion, UserLocation } from "./types";

// ============================================================================
// Location state shared by every screen.
//
//   const { location, coords, openPicker } = useLocation();
//
// - `location` is null until the user picks one.
// - The choice is remembered in the browser (localStorage) between visits.
// - `pickerOpen` / `openPicker()` let any screen open the location picker.
// ============================================================================

const STORAGE_KEY = "storm-watch:location";

export type GpsStatus = "idle" | "locating" | "error";

interface LocationContextValue {
  location: UserLocation | null;
  coords: Coords | null; // handy for the API functions; null until chosen
  setFromSuggestion: (s: LocationSuggestion) => void;
  useMyLocation: () => void; // asks the browser for GPS permission
  clearLocation: () => void;
  gpsStatus: GpsStatus;
  gpsError: string | null;
  pickerOpen: boolean;
  openPicker: () => void;
  closePicker: () => void;
}

const LocationContext = createContext<LocationContextValue | null>(null);

// ---- Browser storage (wrapped: private windows / blocked storage can throw) ----

function isValidLocation(value: unknown): value is UserLocation {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.label === "string" &&
    typeof v.lat === "number" &&
    typeof v.lon === "number"
  );
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
  const [gpsStatus, setGpsStatus] = useState<GpsStatus>("idle");
  const [gpsError, setGpsError] = useState<string | null>(null);
  // First-time visitors with no saved location see the picker right away.
  const [pickerOpen, setPickerOpen] = useState(() => loadSaved() === null);

  const commit = useCallback((next: UserLocation) => {
    setLocationState(next);
    persist(next);
    setPickerOpen(false);
    // TODO: optional server-side copy (see saveUserLocation in the API file).
    saveUserLocation(next).catch(() => {
      /* not critical: the local copy is enough */
    });
  }, []);

  const setFromSuggestion = useCallback(
    (s: LocationSuggestion) => {
      setGpsStatus("idle");
      setGpsError(null);
      commit({ label: s.label, lat: s.lat, lon: s.lon, source: "search" });
    },
    [commit],
  );

  const useMyLocation = useCallback(() => {
    if (!("geolocation" in navigator)) {
      setGpsStatus("error");
      setGpsError("Your browser doesn't support location. Search for a city instead.");
      return;
    }
    setGpsStatus("locating");
    setGpsError(null);

    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const { latitude, longitude } = pos.coords;
        let label = "Your current location";
        try {
          label = await reverseGeocode(latitude, longitude);
        } catch {
          /* keep the generic label if the name lookup fails */
        }
        setGpsStatus("idle");
        commit({ label, lat: latitude, lon: longitude, source: "gps" });
      },
      (err) => {
        setGpsStatus("error");
        setGpsError(
          err.code === err.PERMISSION_DENIED
            ? "Location permission was denied. Allow it in your browser settings, or search for a city."
            : "Couldn't get your location. Try again, or search for a city.",
        );
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 5 * 60 * 1000 },
    );
  }, [commit]);

  const clearLocation = useCallback(() => {
    setLocationState(null);
    persist(null);
    setPickerOpen(true);
  }, []);

  const openPicker = useCallback(() => setPickerOpen(true), []);
  const closePicker = useCallback(() => {
    // Can't dismiss the picker until a location has been chosen.
    if (location) setPickerOpen(false);
  }, [location]);

  const value = useMemo<LocationContextValue>(
    () => ({
      location,
      coords: location ? { lat: location.lat, lon: location.lon } : null,
      setFromSuggestion,
      useMyLocation,
      clearLocation,
      gpsStatus,
      gpsError,
      pickerOpen,
      openPicker,
      closePicker,
    }),
    [
      location,
      setFromSuggestion,
      useMyLocation,
      clearLocation,
      gpsStatus,
      gpsError,
      pickerOpen,
      openPicker,
      closePicker,
    ],
  );

  return (
    <LocationContext.Provider value={value}>{children}</LocationContext.Provider>
  );
}

export function useLocation(): LocationContextValue {
  const ctx = useContext(LocationContext);
  if (!ctx) {
    throw new Error("useLocation must be used inside <LocationProvider>");
  }
  return ctx;
}