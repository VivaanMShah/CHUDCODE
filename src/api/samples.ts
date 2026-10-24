// ============================================================================
// SAMPLE DATA — not connected to the backend.
//
// The outage backend has no weather radar, evacuation zones, public place
// directory, or chatbot. These screens keep their design sample data so the
// app still shows them, and the UI labels them "Sample data".
// (Moved here from node_modules/api/placeholders.ts.)
// ============================================================================

import type {
  ChatMessage,
  ChatReply,
  EvacData,
  RadarData,
  RadarFrame,
  SafetyPlace,
  UserLocation,
} from "../types";

const fakeDelay = (ms = 250) => new Promise((r) => setTimeout(r, ms));

export async function fetchRadar(): Promise<RadarData> {
  await fakeDelay();

  // Sample frames: the same few cells drifting north-east over 50 minutes.
  const minutesAgoSteps = [50, 40, 30, 20, 10, 0];
  const frames: RadarFrame[] = minutesAgoSteps.map((minutesAgo, i) => {
    const drift = (minutesAgoSteps.length - 1 - i) * -1.6; // older = further SW
    return {
      minutesAgo,
      cells: [
        { id: "c1", xMi: -10 + drift, yMi: 12 + drift, intensity: 0.85, radiusMi: 9 },
        { id: "c2", xMi: 6 + drift, yMi: 30 + drift, intensity: 0.7, radiusMi: 7 },
        { id: "c3", xMi: 14 + drift, yMi: 6 + drift, intensity: 0.55, radiusMi: 5 },
        { id: "c4", xMi: -12 + drift, yMi: -9 + drift, intensity: 0.75, radiusMi: 6 },
      ],
    };
  });

  return { isLive: true, updatedAt: "11:12 AM", frames };
}

// Straight-line distance in miles, rounded to 1 decimal.
const milesAway = (xMi: number, yMi: number) =>
  Math.round(Math.hypot(xMi, yMi) * 10) / 10;

export async function fetchSafetyPlaces(): Promise<SafetyPlace[]> {
  await fakeDelay();
  return [
    {
      id: "h1",
      kind: "hospital",
      name: "City General Hospital",
      xMi: 4,
      yMi: 6,
      distanceMi: milesAway(4, 6),
      address: "100 Main St",
      phone: "555-0100",
    },
    {
      id: "h2",
      kind: "hospital",
      name: "Riverside Medical Center",
      xMi: -14,
      yMi: 8,
      distanceMi: milesAway(-14, 8),
      address: "42 River Rd",
      phone: "555-0142",
    },
    {
      id: "s1",
      kind: "shelter",
      name: "Lincoln High School shelter",
      xMi: 6,
      yMi: -12,
      distanceMi: milesAway(6, -12),
      address: "800 Lincoln Ave",
    },
    {
      id: "s2",
      kind: "shelter",
      name: "Community Center shelter",
      xMi: -3,
      yMi: 14,
      distanceMi: milesAway(-3, 14),
      address: "22 Park Blvd",
    },
    {
      id: "f1",
      kind: "fire",
      name: "Fire Station 7",
      xMi: -9,
      yMi: -3,
      distanceMi: milesAway(-9, -3),
    },
    {
      id: "p1",
      kind: "police",
      name: "Central Police Station",
      xMi: 2,
      yMi: -4,
      distanceMi: milesAway(2, -4),
    },
  ];
}

export async function fetchEvacZones(): Promise<EvacData> {
  await fakeDelay();
  return {
    updatedAt: "11:12 AM",
    zones: [
      {
        id: "z1",
        name: "Riverside",
        level: 0.95,
        status: "evacuate",
        polygon: [
          { xMi: -24, yMi: -10 },
          { xMi: -6, yMi: -10 },
          { xMi: -6, yMi: 18 },
          { xMi: -24, yMi: 18 },
        ],
        instruction: "Evacuate now. Head east on Route 12 to higher ground.",
      },
      {
        id: "z2",
        name: "Central",
        level: 0.6,
        status: "prepare",
        polygon: [
          { xMi: -6, yMi: -10 },
          { xMi: 12, yMi: -10 },
          { xMi: 12, yMi: 10 },
          { xMi: -6, yMi: 10 },
        ],
        instruction: "Be ready to leave. Pack essentials and know your route.",
      },
      {
        id: "z3",
        name: "North Hills",
        level: 0.7,
        status: "prepare",
        polygon: [
          { xMi: -6, yMi: 10 },
          { xMi: 12, yMi: 10 },
          { xMi: 12, yMi: 34 },
          { xMi: -6, yMi: 34 },
        ],
        instruction: "Be ready to leave. Flooding possible on low roads.",
      },
      {
        id: "z4",
        name: "East County",
        level: 0.35,
        status: "monitor",
        polygon: [
          { xMi: 12, yMi: -10 },
          { xMi: 34, yMi: -10 },
          { xMi: 34, yMi: 16 },
          { xMi: 12, yMi: 16 },
        ],
        instruction: "Stay alert. Conditions may change within the hour.",
      },
      {
        id: "z5",
        name: "South Valley",
        level: 0.1,
        status: "safe",
        polygon: [
          { xMi: -24, yMi: -34 },
          { xMi: 24, yMi: -34 },
          { xMi: 24, yMi: -10 },
          { xMi: -24, yMi: -10 },
        ],
        instruction: "No evacuation needed. Shelter-in-place if storms arrive.",
      },
      {
        id: "z6",
        name: "West Plains",
        level: 0.05,
        status: "safe",
        polygon: [
          { xMi: -44, yMi: -34 },
          { xMi: -24, yMi: -34 },
          { xMi: -24, yMi: 30 },
          { xMi: -44, yMi: 30 },
        ],
        instruction: "No evacuation needed.",
      },
      {
        id: "z7",
        name: "Northwest",
        level: 0.4,
        status: "monitor",
        polygon: [
          { xMi: -24, yMi: 18 },
          { xMi: -6, yMi: 18 },
          { xMi: -6, yMi: 34 },
          { xMi: -24, yMi: 34 },
        ],
        instruction: "Stay alert. Check for updates every 15 minutes.",
      },
    ],
  };
}

export async function sendChatMessage(
  question: string,
  _location: UserLocation | null,
  _history: ChatMessage[],
): Promise<ChatReply> {
  await fakeDelay(700);

  const q = question.toLowerCase();
  const where = _location?.label ?? "your area";

  if (/(how bad|worst|severe|dangerous|predict|forecast|get worse)/.test(q)) {
    return {
      text: `Sample answer for ${where}: storm risk is high through the evening, with damaging wind gusts up to about 52 mph and possible large hail. The peak looks like 4:00 to 7:00 PM. This is placeholder text. Your real chatbot will use live forecast data here.`,
      suggestions: ["Should I evacuate?", "When will it be over?"],
    };
  }
  if (/(evacuat|leave|flood|shelter)/.test(q)) {
    return {
      text: "Sample answer: check the Radar tab's evacuation map. Red areas should leave now, orange areas should be ready to go, and green areas are safe. Always follow instructions from local emergency officials. In an emergency, call 911.",
      suggestions: ["Where is the nearest shelter?", "Where is the nearest hospital?"],
    };
  }
  if (/(hospital|shelter|fire|police)/.test(q)) {
    return {
      text: "Sample answer: the closest hospital is City General Hospital, about 7 miles away. You can see hospitals, shelters, fire stations and police stations on the Radar map.",
      suggestions: ["What should I pack?", "How bad will it get?"],
    };
  }
  if (/(pack|prepare|kit|ready)/.test(q)) {
    return {
      text: "Sample answer: charge phones and power banks, gather water, medications, documents and a flashlight, bring in loose outdoor items, and know where your shelter is.",
    };
  }
  return {
    text: "This is a placeholder reply. Once your backend is connected, I'll answer questions about forecasts, how severe the storm may get, and what to do.",
    suggestions: ["How bad will it get?", "Should I evacuate?", "What should I pack?"],
  };
}