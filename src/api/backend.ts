// ============================================================================
// Real backend calls (FastAPI in app/).
//
// Dev:  `npm run dev` proxies /api/* to http://localhost:8000 (see vite.config.ts).
// Prod: set VITE_API_URL to the deployed API, e.g. https://api.example.com
// ============================================================================

import type {
  AssessResponse,
  LocationsResponse,
  MessageResponse,
  MessageType,
  Scenario,
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

// The area list never changes while the server runs, so fetch it once.
let locationsCache: Promise<LocationsResponse> | null = null;

export function getLocations(): Promise<LocationsResponse> {
  if (!locationsCache) {
    locationsCache = call<LocationsResponse>("/locations").catch((e) => {
      locationsCache = null; // allow a retry after a failure
      throw e;
    });
  }
  return locationsCache;
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
