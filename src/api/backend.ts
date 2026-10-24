// ============================================================================
// Real backend calls (FastAPI in app/).
//
// Dev:  `npm run dev` proxies /api/* to http://localhost:8000 (see vite.config.ts).
// Prod: set VITE_API_URL to the deployed API, e.g. https://api.example.com
// ============================================================================

import type {
  AssessResponse,
  ChatResponse,
  MessageResponse,
  MessageType,
  Scenario,
  WeatherResponse,
  ZipResponse,
} from "./contract";

const API_BASE = (import.meta.env.VITE_API_URL ?? "/api").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    throw new ApiError(res.status, `${res.status} ${await res.text().catch(() => "")}`);
  }
  return (await res.json()) as T;
}

// ZIP search: the only thing a customer types. Returns an area id, never coordinates.
export function lookupZip(zip: string, signal?: AbortSignal): Promise<ZipResponse> {
  return call<ZipResponse>(`/zip/${encodeURIComponent(zip)}`, { signal });
}

// Live storm strength (1–5), next 12 hours, and National Weather Service alerts.
export function getWeather(zip: string, signal?: AbortSignal): Promise<WeatherResponse> {
  return call<WeatherResponse>(`/weather/${encodeURIComponent(zip)}`, { signal });
}

// Ask the concierge. The server answers from the estimate's facts only and checks every number.
export function askConcierge(
  locationId: string,
  scenario: Scenario,
  question: string,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  return call<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify({ location_id: locationId, question, scenario }),
    signal,
  });
}

export function assess(
  locationId: string,
  scenario: Scenario,
  signal?: AbortSignal,
): Promise<AssessResponse> {
  return call<AssessResponse>("/assess", {
    method: "POST",
    body: JSON.stringify({ location_id: locationId, scenario }),
    signal,
  });
}

export function getMessage(
  locationId: string,
  messageType: MessageType,
  scenario: Scenario,
  signal?: AbortSignal,
): Promise<MessageResponse> {
  return call<MessageResponse>("/message", {
    method: "POST",
    body: JSON.stringify({ location_id: locationId, message_type: messageType, scenario }),
    signal,
  });
}
