import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { ZipResponse } from "./api/contract";

// ============================================================================
// The customer's ZIP code, shared by every screen. A ZIP is the only thing we
// ask for. It is saved on this device only (localStorage), never as an account.
//
//   const { location, openPicker } = useLocation();
// ============================================================================

const STORAGE_KEY = "storm-watch:zip";

export interface UserLocation {
  zip: string; // "24014"
  label: string; // "ZIP 24014 · Roanoke"
  locationId: string; // "ZIP-24014"
}

interface LocationContextValue {
  location: UserLocation | null;
  setFromZip: (z: ZipResponse) => void;
  clearLocation: () => void;
  pickerOpen: boolean;
  openPicker: () => void;
  closePicker: () => void;
}

const LocationContext = createContext<LocationContextValue | null>(null);

function loadSaved(): UserLocation | null {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    const o = v as Record<string, unknown> | null;
    return o && typeof o.zip === "string" && typeof o.label === "string" && typeof o.locationId === "string"
      ? (o as unknown as UserLocation)
      : null;
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

export function LocationProvider({ children }: { children: ReactNode }) {
  const [location, setLocation] = useState<UserLocation | null>(loadSaved);
  // First-time visitors with no saved ZIP see the picker right away.
  const [pickerOpen, setPickerOpen] = useState(() => loadSaved() === null);

  const setFromZip = useCallback((z: ZipResponse) => {
    const next = { zip: z.zip, label: z.label, locationId: z.location_id };
    setLocation(next);
    persist(next);
    setPickerOpen(false);
  }, []);

  const clearLocation = useCallback(() => {
    setLocation(null);
    persist(null);
    setPickerOpen(true);
  }, []);

  const openPicker = useCallback(() => setPickerOpen(true), []);
  const closePicker = useCallback(() => {
    if (location) setPickerOpen(false); // can't dismiss until a ZIP is set
  }, [location]);

  const value = useMemo(
    () => ({ location, setFromZip, clearLocation, pickerOpen, openPicker, closePicker }),
    [location, setFromZip, clearLocation, pickerOpen, openPicker, closePicker],
  );
  return <LocationContext.Provider value={value}>{children}</LocationContext.Provider>;
}

export function useLocation(): LocationContextValue {
  const ctx = useContext(LocationContext);
  if (!ctx) throw new Error("useLocation must be used inside <LocationProvider>");
  return ctx;
}
