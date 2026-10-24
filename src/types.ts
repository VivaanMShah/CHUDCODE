// ============================================================================
// Nubus — shared types
// UI-side shapes. Backend response shapes live in src/api/contract.ts;
// Chat message shape used by the Chat screen.
// ============================================================================

export type TabId = "home" | "radar" | "alerts" | "storm" | "chat";

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

// ---- Chatbot ------------------------------------------------------------------

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  text: string;
  sentAt: string; // "11:14 AM"
  checked?: boolean; // assistant answers written by the model, with every number verified
}

// What the backend sends back for each question.
export interface ChatReply {
  text: string;
  // Optional tappable follow-up questions shown under the reply.
  suggestions?: string[];
}