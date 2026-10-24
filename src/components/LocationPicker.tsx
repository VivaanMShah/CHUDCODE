import { useEffect, useRef, useState } from "react";
import { getLocations } from "../api/backend";
import { useLocation } from "../location";
import type { LocationSuggestion } from "../types";
import "./LocationPicker.css";

// Full-screen sheet (inside the phone-width app frame) where the user picks
// their area from the backend's anonymized area list (GET /locations).
export default function LocationPicker() {
  const { location, pickerOpen, closePicker, setFromSuggestion } = useLocation();

  const [query, setQuery] = useState("");
  const [areas, setAreas] = useState<LocationSuggestion[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // Focus the search box whenever the sheet opens; reset it when it closes.
  useEffect(() => {
    if (pickerOpen) {
      inputRef.current?.focus();
    } else {
      setQuery("");
    }
  }, [pickerOpen]);

  // Load the area list from the backend when the sheet opens.
  useEffect(() => {
    if (!pickerOpen || areas) return;
    let cancelled = false;
    setSearching(true);
    getLocations()
      .then((r) => {
        if (cancelled) return;
        setAreas(
          r.locations.map((l) => ({
            id: l.location_id,
            label: l.label,
            customersServedBand: l.customers_served_band,
            hasStormHistory: l.has_storm_history,
          })),
        );
        setSearchError(false);
      })
      .catch(() => !cancelled && setSearchError(true))
      .finally(() => !cancelled && setSearching(false));
    return () => {
      cancelled = true;
    };
  }, [pickerOpen, areas, attempt]);

  const trimmed = query.trim().toLowerCase();
  const results = (areas ?? []).filter(
    (a) => !trimmed || a.label.toLowerCase().includes(trimmed) || a.id.toLowerCase().includes(trimmed),
  );

  // Escape closes the sheet (only allowed once a location exists).
  useEffect(() => {
    if (!pickerOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closePicker();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pickerOpen, closePicker]);

  if (!pickerOpen) return null;

  const canClose = location !== null;
  const showEmpty = areas !== null && results.length === 0;

  return (
    <div
      className="picker"
      role="dialog"
      aria-modal="true"
      aria-labelledby="picker-title"
    >
      <div className="picker__sheet">
        <header className="picker__header">
          <div>
            <h2 id="picker-title">
              {canClose ? "Change area" : "Pick your area"}
            </h2>
            <p>
              {canClose
                ? `Currently: ${location.label}`
                : "We use your area to estimate outage risk and how long repairs may take."}
            </p>
          </div>
          {canClose && (
            <button
              type="button"
              className="picker__close"
              onClick={closePicker}
              aria-label="Close"
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                aria-hidden
              >
                <path d="M6 6l12 12M18 6 6 18" />
              </svg>
            </button>
          )}
        </header>

        {/* Search */}
        <label className="picker__search">
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            aria-hidden
          >
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-3.5-3.5" />
          </svg>
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search areas, e.g. Roanoke or Salem"
            autoComplete="off"
            aria-label="Search for an area"
          />
        </label>

        <ul className="picker__results" aria-live="polite">
          {searching && <li className="picker__note">Loading areas…</li>}
          {searchError && (
            <li className="picker__note picker__note--error">
              Couldn't load areas. Check that the backend is running.{" "}
              <button type="button" className="picker__retry" onClick={() => setAttempt((n) => n + 1)}>
                Try again
              </button>
            </li>
          )}
          {showEmpty && (
            <li className="picker__note">No areas match “{query.trim()}”.</li>
          )}
          {results.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                className="picker__result"
                onClick={() => setFromSuggestion(r)}
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden
                >
                  <path d="M12 21s-7-6.2-7-11.2A7 7 0 0 1 19 9.8C19 14.8 12 21 12 21Z" />
                  <circle cx="12" cy="9.8" r="2.4" />
                </svg>
                <span className="picker__result-text">
                  <span>{r.label}</span>
                  <small>
                    Serves {r.customersServedBand} customers
                    {r.hasStormHistory ? "" : " · no storm history"}
                  </small>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}