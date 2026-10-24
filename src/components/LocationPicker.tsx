import { useEffect, useRef, useState } from "react";
import { searchLocations } from "api/placeholders";
import { useLocation } from "../location";
import type { LocationSuggestion } from "../types";
import "./LocationPicker.css";

// Full-screen sheet (inside the phone-width app frame) where the user chooses
// their location: type a city, or tap "Use my current location".
export default function LocationPicker() {
  const {
    location,
    pickerOpen,
    closePicker,
    setFromSuggestion,
    useMyLocation,
    gpsStatus,
    gpsError,
  } = useLocation();

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<LocationSuggestion[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // Focus the search box whenever the sheet opens; reset it when it closes.
  useEffect(() => {
    if (pickerOpen) {
      inputRef.current?.focus();
    } else {
      setQuery("");
      setResults([]);
      setSearchError(false);
    }
  }, [pickerOpen]);

  // Search as the user types, waiting 250ms after the last keystroke.
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setResults([]);
      setSearching(false);
      setSearchError(false);
      return;
    }

    let cancelled = false;
    setSearching(true);
    const timer = setTimeout(() => {
      searchLocations(q)
        .then((r) => {
          if (cancelled) return;
          setResults(r);
          setSearchError(false);
        })
        .catch(() => !cancelled && setSearchError(true))
        .finally(() => !cancelled && setSearching(false));
    }, 250);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

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
  const trimmed = query.trim();
  const showEmpty =
    trimmed.length >= 2 && !searching && !searchError && results.length === 0;

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
              {canClose ? "Change location" : "Where are you?"}
            </h2>
            <p>
              {canClose
                ? `Currently: ${location.label}`
                : "We use your location to show storms, alerts and safe places near you."}
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

        {/* Use GPS */}
        <button
          type="button"
          className="picker__gps"
          onClick={useMyLocation}
          disabled={gpsStatus === "locating"}
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <circle cx="12" cy="12" r="3.2" />
            <circle cx="12" cy="12" r="7.5" />
            <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
          </svg>
          {gpsStatus === "locating" ? "Finding you…" : "Use my current location"}
        </button>

        {gpsError && (
          <p className="picker__error" role="alert">
            {gpsError}
          </p>
        )}

        <div className="picker__divider">
          <span>or search</span>
        </div>

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
            placeholder="City, ZIP or address"
            autoComplete="off"
            aria-label="Search for a location"
          />
        </label>

        <ul className="picker__results" aria-live="polite">
          {searching && <li className="picker__note">Searching…</li>}
          {searchError && (
            <li className="picker__note picker__note--error">
              Search failed. Check your connection and try again.
            </li>
          )}
          {showEmpty && (
            <li className="picker__note">No matches for “{trimmed}”.</li>
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
                <span>{r.label}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}