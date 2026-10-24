import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Channel, DeclaredNeed } from "./api/contract";
import { useAssessment } from "./assessment";

// ============================================================================
// "About you": optional details that make the tips fit the person.
// Saved on this device only. We never ask for a name, address, or exact location.
// Only the needs (medical equipment, accessibility, critical facility) and backup hours
// are sent with an estimate request, exactly as before. Everything else stays here.
// ============================================================================

export type Household = "kids" | "older" | "pets" | "alone";
export type HomeType = "house" | "apartment" | "mobile";
export type Extra = "well" | "electric_heat" | "generator";

export interface Profile {
  household: Household[];
  home: HomeType | null;
  extras: Extra[];
  needs: DeclaredNeed[];
  backupHours: number | null;
  contact: Channel | null;
}

export const EMPTY_PROFILE: Profile = { household: [], home: null, extras: [], needs: [], backupHours: null, contact: null };

const KEY = "storm-watch:profile";
const DISMISSED_KEY = "storm-watch:profile-prompt-dismissed";

function load(): Profile {
  try {
    const p = JSON.parse(localStorage.getItem(KEY) ?? "null") as Partial<Profile> | null;
    return p ? { ...EMPTY_PROFILE, ...p } : EMPTY_PROFILE;
  } catch {
    return EMPTY_PROFILE;
  }
}

interface ProfileContextValue {
  profile: Profile;
  update: (patch: Partial<Profile>) => void;
  clear: () => void;
  isEmpty: boolean;
  sheetOpen: boolean;
  openSheet: () => void;
  closeSheet: () => void;
  promptDismissed: boolean;
  dismissPrompt: () => void;
}

const ProfileContext = createContext<ProfileContextValue | null>(null);

export function ProfileProvider({ children }: { children: ReactNode }) {
  const { updateScenario } = useAssessment();
  const [profile, setProfile] = useState<Profile>(load);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [promptDismissed, setPromptDismissed] = useState(() => {
    try {
      return localStorage.getItem(DISMISSED_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(profile));
    } catch {
      /* not critical */
    }
  }, [profile]);

  // Needs and backup hours feed the estimate. Nothing else leaves the device.
  useEffect(() => {
    updateScenario({
      declared_needs: profile.needs,
      backup_hours: profile.needs.includes("medical_equipment") ? profile.backupHours : null,
    });
  }, [profile.needs, profile.backupHours, updateScenario]);

  const update = useCallback((patch: Partial<Profile>) => setProfile((p) => ({ ...p, ...patch })), []);
  const clear = useCallback(() => setProfile(EMPTY_PROFILE), []);
  const dismissPrompt = useCallback(() => {
    setPromptDismissed(true);
    try {
      localStorage.setItem(DISMISSED_KEY, "1");
    } catch {
      /* not critical */
    }
  }, []);

  const isEmpty = JSON.stringify(profile) === JSON.stringify(EMPTY_PROFILE);
  const value = useMemo(
    () => ({
      profile, update, clear, isEmpty, sheetOpen, openSheet: () => setSheetOpen(true),
      closeSheet: () => setSheetOpen(false), promptDismissed, dismissPrompt,
    }),
    [profile, update, clear, isEmpty, sheetOpen, promptDismissed, dismissPrompt],
  );
  return <ProfileContext.Provider value={value}>{children}</ProfileContext.Provider>;
}

export function useProfile(): ProfileContextValue {
  const ctx = useContext(ProfileContext);
  if (!ctx) throw new Error("useProfile must be used inside <ProfileProvider>");
  return ctx;
}

// Extra tips for this person. Plain words, no guessing: each one comes from something they chose.
export function tipsFor(p: Profile, ctx: { severity: number; tempF: number | null }): string[] {
  const t: string[] = [];
  if (p.household.includes("older")) t.push("Keep at least a week of medicines on hand, and check on older family or neighbors.");
  if (p.household.includes("kids")) t.push("Charge tablets and keep books, games, and snacks ready for children.");
  if (p.household.includes("pets")) t.push("Set aside extra water and food for your pets, and keep carriers handy.");
  if (p.household.includes("alone")) t.push("Tell a neighbor or friend your plan, and set a time to check in.");
  if (p.extras.includes("well")) t.push("Fill containers with water now. Your well pump stops when the power does.");
  if (p.extras.includes("electric_heat") && (ctx.tempF ?? 99) < 50) {
    t.push("If the heat goes out, pick one warm room, close the door, and layer blankets.");
  }
  if (p.extras.includes("generator")) t.push("Run your generator outside, at least 20 feet from doors and windows.");
  if (p.home === "apartment") t.push("Ask your building if the elevator has backup power, and plan for stairs.");
  if (p.home === "mobile" && ctx.severity >= 4) t.push("Mobile homes are less safe in high wind. Plan to shelter somewhere sturdier.");
  return t;
}
