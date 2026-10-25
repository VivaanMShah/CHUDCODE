import { useEffect, useRef, useState } from "react";
import { ApiError, lookupZip } from "../api/backend";
import { useLocation } from "../location";
import "./LocationPicker.css";

const EXAMPLES = ["24014", "24153", "24179"]; // Roanoke, Salem, Vinton

type Status = { kind: "idle" } | { kind: "loading" } | { kind: "error"; message: string };

// Sheet where the customer enters a ZIP code. That is the only thing we ask for.
export default function ZipPicker() {
  const { location, pickerOpen, closePicker, setFromZip } = useLocation();
  const [zip, setZip] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (pickerOpen) inputRef.current?.focus();
    else {
      setZip("");
      setStatus({ kind: "idle" });
    }
  }, [pickerOpen]);

  useEffect(() => {
    if (!pickerOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closePicker();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pickerOpen, closePicker]);

  function search(z: string) {
    setStatus({ kind: "loading" });
    lookupZip(z)
      .then(setFromZip)
      .catch((e: unknown) =>
        setStatus({
          kind: "error",
          message:
            e instanceof ApiError && e.status === 404
              ? "We don't have outage data for that ZIP code yet. Try a nearby one."
              : "Couldn't look that up. Check your connection and try again.",
        }),
      );
  }

  function onChange(value: string) {
    const digits = value.replace(/\D/g, "").slice(0, 5);
    setZip(digits);
    setStatus({ kind: "idle" });
    if (digits.length === 5) search(digits); // look up as soon as the 5th digit is typed
  }

  if (!pickerOpen) return null;

  return (
    <div className="picker" role="dialog" aria-modal="true" aria-labelledby="picker-title">
      <div className="picker__sheet">
        <header className="picker__header">
          <div>
            {!location && <img className="picker__logo" src="/logo.png" alt="Storm Watch" width={56} height={56} />}
            <h2 id="picker-title">{location ? "Change ZIP code" : "What's your ZIP code?"}</h2>
            <p>
              {location
                ? `Currently ${location.label}`
                : "We'll show your outage risk and how to get ready. That's all we need."}
            </p>
          </div>
          {location && (
            <button type="button" className="picker__close" onClick={closePicker} aria-label="Close">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                strokeWidth="2" strokeLinecap="round" aria-hidden>
                <path d="M6 6l12 12M18 6 6 18" />
              </svg>
            </button>
          )}
        </header>

        <label className="picker__search picker__zip">
          <input
            ref={inputRef}
            type="text"
            inputMode="numeric"
            autoComplete="postal-code"
            pattern="[0-9]*"
            maxLength={5}
            value={zip}
            onChange={(e) => onChange(e.target.value)}
            placeholder="5-digit ZIP"
            aria-label="ZIP code"
            aria-describedby="zip-help"
          />
        </label>

        <div id="zip-help" aria-live="polite">
          {status.kind === "loading" && <p className="picker__note">Looking up your area…</p>}
          {status.kind === "error" && <p className="picker__note picker__note--error">{status.message}</p>}
        </div>

        <div className="picker__examples">
          <span>Try</span>
          {EXAMPLES.map((z) => (
            <button key={z} type="button" className="chip" onClick={() => onChange(z)}>
              {z}
            </button>
          ))}
        </div>

        <p className="picker__privacy">
          We only use your ZIP code. We don't collect your name, address, or exact location. It stays
          on this device.
        </p>
      </div>
    </div>
  );
}
