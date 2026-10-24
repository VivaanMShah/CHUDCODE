// ============================================================================
// Nubus — shared types
// These describe the data the UI expects. Your backend should return objects
// matching these shapes (see src/api/placeholders.ts).
// ============================================================================

export type TabId = "home" | "radar" | "alerts" | "storm" | "chat";

// ---- User location ----------------------------------------------------------

export type LocationSource = "gps" | "search" | "saved";

export interface UserLocation {
  label: string; // "Columbus, OH"
  lat: number;
  lon: number;
  source: LocationSource;
}

// What the search box shows as the user types (see searchLocations in the API).
export interface LocationSuggestion {
  id: string;
  label: string; // "Columbus, OH, USA"
  lat: number;
  lon: number;
}

// ---- Home ------------------------------------------------------------------

export interface HomeSummary {
  locationName: string; // "YOUR CITY, ST"
  updatedAt: string; // "11:12 AM"
  temperatureF: number; // 58
  conditionLabel: string; // "Thunderstorms nearby"
  topAlert: TopAlert | null; // red banner at the top of Home
  stormRisk: StormRisk;
  hourlyChance: HourlyChance[];
  stats: HomeStats;
}

export interface TopAlert {
  id: string;
  title: string; // "Flash flood warning until 6:30 PM"
  actionLabel: string; // "View details"
}

export type RiskLevel = "Low" | "Moderate" | "High" | "Extreme";

export interface StormRisk {
  percent: number; // 0–100
  level: RiskLevel;
  windowLabel: string; // "NEXT 6 HOURS"
  summary: string; // "Severe storms likely between 4:00 and 7:00 PM. ..."
}

export interface HourlyChance {
  label: string; // "Now", "2 PM", ...
  percent: number; // 0–100
}

export interface HomeStats {
  windGustMph: number;
  pressureInHg: number;
  lightningCount: number;
  lightningRadiusMi: number;
  lightningWindowMin: number;
}

// ---- Radar -----------------------------------------------------------------

export type RadarLayer = "rain" | "lightning" | "wind" | "clouds";

export interface RadarCell {
  id: string;
  // Position relative to the user, in miles (x = east, y = north).
  xMi: number;
  yMi: number;
  intensity: number; // 0–1 (light → heavy)
  radiusMi: number; // visual size of the blob
}

export interface RadarFrame {
  minutesAgo: number; // 50, 40, ... 0 (0 = "Now")
  cells: RadarCell[];
}

export interface RadarData {
  isLive: boolean;
  updatedAt: string; // "11:12 AM"
  frames: RadarFrame[]; // oldest → newest
}

// ---- Alerts ----------------------------------------------------------------

export type AlertSeverity = "warning" | "watch" | "advisory";

export interface WeatherAlert {
  id: string;
  severity: AlertSeverity;
  title: string; // "Flash flood warning"
  untilLabel: string; // "Until 6:30 PM"
  description: string;
  whatToDo: string;
}

export interface AlertsData {
  locationName: string;
  alerts: WeatherAlert[];
  pushEnabled: boolean;
  pushThresholdPercent: number; // 60
}

// ---- Storm tracker ----------------------------------------------------------

export interface PathStop {
  id: string;
  label: string; // "West County", "Your location", "East County"
  timeLabel: string; // "Passed 3:50 PM" | "Expected 4:40 PM"
  status: "passed" | "you" | "upcoming";
}

export interface PrepItem {
  id: string;
  label: string;
  done: boolean;
}

export interface StormTrack {
  cellName: string; // "Cell A"
  cellType: string; // "Severe thunderstorm"
  etaLabel: string; // "4:40 PM"
  etaRelative: string; // "In 2 h 14 min"
  updatedAgo: string; // "1 min ago"
  speedMph: number;
  heading: string; // "NE"
  hailInches: number;
  peakGustMph: number;
  path: PathStop[];
  prepare: PrepItem[];
}

// ---- Radar: safety places ----------------------------------------------------

export type SafetyPlaceKind = "hospital" | "shelter" | "fire" | "police";

export interface SafetyPlace {
  id: string;
  kind: SafetyPlaceKind;
  name: string; // "City General Hospital"
  // Position relative to the user, in miles (x = east, y = north).
  xMi: number;
  yMi: number;
  distanceMi: number; // straight-line distance from the user
  address?: string;
  phone?: string;
}

// ---- Radar: evacuation zones (traffic-light overlay) --------------------------

export type EvacStatus = "safe" | "monitor" | "prepare" | "evacuate";

export interface MilePoint {
  xMi: number;
  yMi: number;
}

export interface EvacZone {
  id: string;
  name: string; // "Riverside"
  // 0 = completely safe (green) … 1 = evacuate now (red). Shades in between
  // (yellow, orange) are blended from this number.
  level: number;
  status: EvacStatus;
  polygon: MilePoint[]; // outline of the zone, in miles from the user
  instruction: string; // "Leave now via Route 12 heading west."
}

export interface EvacData {
  updatedAt: string; // "11:12 AM"
  zones: EvacZone[];
}

// ---- Chatbot ------------------------------------------------------------------

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  text: string;
  sentAt: string; // "11:14 AM"
}

// What the backend sends back for each question.
export interface ChatReply {
  text: string;
  // Optional tappable follow-up questions shown under the reply.
  suggestions?: string[];
}