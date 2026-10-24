import { useEffect } from "react";
import type { Channel, DeclaredNeed } from "../api/contract";
import { CHANNEL_LABEL } from "../assessment";
import { useProfile, type Extra, type Household, type HomeType } from "../profile";
import "./LocationPicker.css";

const HOUSEHOLD: { id: Household; label: string }[] = [
  { id: "kids", label: "Children" },
  { id: "older", label: "Older adults" },
  { id: "pets", label: "Pets" },
  { id: "alone", label: "I live alone" },
];
const HOMES: { id: HomeType; label: string }[] = [
  { id: "house", label: "House" },
  { id: "apartment", label: "Apartment" },
  { id: "mobile", label: "Mobile home" },
];
const EXTRAS: { id: Extra; label: string }[] = [
  { id: "well", label: "Well water pump" },
  { id: "electric_heat", label: "Electric heat" },
  { id: "generator", label: "I have a generator" },
];
const NEEDS: { id: DeclaredNeed; label: string }[] = [
  { id: "medical_equipment", label: "Medical equipment that needs power" },
  { id: "accessibility", label: "Mobility or accessibility needs" },
  { id: "critical_facility", label: "I run a critical facility" },
];
const CONTACTS: Channel[] = ["sms", "push", "email", "phone_call"];

function toggle<T>(list: T[], item: T): T[] {
  return list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
}

// Optional sheet. Everything here is saved on this device only.
export default function AboutMe() {
  const { profile, update, clear, sheetOpen, closeSheet } = useProfile();

  useEffect(() => {
    if (!sheetOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeSheet();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sheetOpen, closeSheet]);

  if (!sheetOpen) return null;
  const hasMedical = profile.needs.includes("medical_equipment");

  return (
    <div className="picker" role="dialog" aria-modal="true" aria-labelledby="about-title">
      <div className="picker__sheet about-me">
        <header className="picker__header">
          <div>
            <h2 id="about-title">About you</h2>
            <p>Optional. It makes the tips fit you. We never ask for your name or address.</p>
          </div>
          <button type="button" className="picker__close" onClick={closeSheet} aria-label="Close">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" aria-hidden>
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </header>

        <div className="about-me__body">
          <fieldset>
            <legend>Who is at home?</legend>
            <div className="chips">
              {HOUSEHOLD.map((h) => (
                <button key={h.id} type="button" aria-pressed={profile.household.includes(h.id)}
                  className={`chip${profile.household.includes(h.id) ? " is-on" : ""}`}
                  onClick={() => update({ household: toggle(profile.household, h.id) })}>{h.label}</button>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend>Your home</legend>
            <div className="chips">
              {HOMES.map((h) => (
                <button key={h.id} type="button" aria-pressed={profile.home === h.id}
                  className={`chip${profile.home === h.id ? " is-on" : ""}`}
                  onClick={() => update({ home: profile.home === h.id ? null : h.id })}>{h.label}</button>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend>Good to know</legend>
            <div className="chips">
              {EXTRAS.map((x) => (
                <button key={x.id} type="button" aria-pressed={profile.extras.includes(x.id)}
                  className={`chip${profile.extras.includes(x.id) ? " is-on" : ""}`}
                  onClick={() => update({ extras: toggle(profile.extras, x.id) })}>{x.label}</button>
              ))}
            </div>
          </fieldset>

          <fieldset>
            <legend>Power needs</legend>
            <p className="about-me__hint">We only use what you tell us. We never guess.</p>
            <div className="chips">
              {NEEDS.map((n) => (
                <button key={n.id} type="button" aria-pressed={profile.needs.includes(n.id)}
                  className={`chip${profile.needs.includes(n.id) ? " is-on" : ""}`}
                  onClick={() => update({ needs: toggle(profile.needs, n.id) })}>{n.label}</button>
              ))}
            </div>
            {hasMedical && (
              <label className="about-me__slider">
                <span>How long does your backup power last? <strong>{profile.backupHours ?? 8} h</strong></span>
                <input type="range" min={1} max={72} value={profile.backupHours ?? 8}
                  onChange={(e) => update({ backupHours: Number(e.target.value) })} />
              </label>
            )}
          </fieldset>

          <fieldset>
            <legend>How should we reach you?</legend>
            <div className="chips">
              {CONTACTS.map((c) => (
                <button key={c} type="button" aria-pressed={profile.contact === c}
                  className={`chip${profile.contact === c ? " is-on" : ""}`}
                  onClick={() => update({ contact: profile.contact === c ? null : c })}>{CHANNEL_LABEL[c]}</button>
              ))}
            </div>
          </fieldset>
        </div>

        <p className="picker__privacy">
          Saved on this device only. When you ask for an estimate, only your power needs and backup hours
          are sent. Everything else stays here.
        </p>
        <div className="about-me__actions">
          <button type="button" className="link" onClick={clear}>Clear all</button>
          <button type="button" className="about-me__done" onClick={closeSheet}>Done</button>
        </div>
      </div>
    </div>
  );
}
