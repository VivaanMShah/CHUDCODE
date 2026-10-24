// ============================================================================
// Backend API contract — mirrors app/models.py. Keep the two in sync.
// ============================================================================

export type Band = "low" | "medium" | "high";
export type DeclaredNeed = "medical_equipment" | "accessibility" | "critical_facility";
export type MessageType =
  | "pre_storm"
  | "outage_confirmed"
  | "estimate_updated"
  | "restoration_complete";
export type Channel = "sms" | "push" | "email" | "phone_call";

export interface Scenario {
  storm_severity: number; // 1–5
  damaged_poles: number; // 0–200
  crews: number; // 1–50
  access_ok: boolean;
  declared_needs: DeclaredNeed[]; // opt-in only
  backup_hours?: number | null; // opt-in, used with medical_equipment
}

export interface Area {
  location_id: string;
  label: string;
  customers_served_band: string;
  has_storm_history: boolean;
}

export interface LocationsResponse {
  locations: Area[];
  data_mode: "mock" | "live";
}

export interface ConfirmedFact {
  fact: string;
  text: string;
  source: string;
  value: number | null;
}

export interface Reason {
  factor: string;
  text: string;
  direction: "increases_risk" | "decreases_risk";
}

export interface RestorationRange {
  low_hours: number;
  high_hours: number;
  median_hours: number;
  method: string;
}

export interface FactorDelta {
  change: string;
  new_range: RestorationRange;
  delta_text: string;
  scenario_patch?: Partial<Scenario> | null; // apply to the sliders to try this change
}

export interface HistogramBin {
  low_hours: number;
  high_hours: number;
  share: number; // 0–1
}

export interface ModelEstimate {
  risk_band: Band;
  risk_score: number; // 0–1
  confidence: Band;
  confidence_reason: string;
  reasons: Reason[];
  restoration_range: RestorationRange;
  factor_deltas: FactorDelta[];
  alert_eligible: boolean;
  alert_rule: string;
  restoration_histogram?: HistogramBin[] | null;
  simulations?: number | null;
}

export interface CustomerActions {
  actions: string[];
  safety_messages: string[];
  channel: Channel;
  send_timing: string;
  escalation: { trigger: string; action: string };
}

export interface Meta {
  fallback: boolean;
  fallback_reason: string | null;
  assumptions: string[];
  limits: string[];
  data_mode: "mock" | "live";
  request_id: string;
  backtest?: Backtest | null;
}

export interface Backtest {
  event_label: string;
  areas: number;
  auc: number;
  high_band_hit_rate: number | null;
  low_band_hit_rate: number | null;
  high_band_areas: number;
  low_band_areas: number;
}

export interface ChainStep {
  device_type: string;
  customers_approx: number | null;
}

export interface AreaContext {
  label: string;
  customers_served_band: string;
  upstream_device_type: string | null;
  customers_on_upstream_device_band: string | null;
  critical_facility_nearby: boolean | null;
  upstream_chain?: ChainStep[] | null; // substation down to this area
}

export interface AssessResponse {
  location_id: string;
  confirmed: ConfirmedFact[];
  model_estimate: ModelEstimate;
  customer_actions: CustomerActions;
  meta: Meta;
  area?: AreaContext | null;
}

export interface MessageResponse {
  location_id: string;
  message_type: MessageType;
  text: string;
  channel: Channel;
  send_timing: string;
  validated: boolean;
  meta: Meta;
}

// ---- ZIP search and live weather ------------------------------------------------

export interface ZipResponse {
  zip: string;
  location_id: string; // "ZIP-24014": use with /assess, /message
  label: string; // "ZIP 24014 · Roanoke"
  city: string;
  customers_served_band: string;
}

export interface WeatherHour {
  hours_ahead: number;
  label: string;
  precip_in: number;
  gust_mph: number;
  wind_from_deg: number;
  temp_f: number;
}

export interface NwsAlert {
  event: string;
  severity: string;
  headline: string;
  ends: string | null;
  instruction: string | null;
}

export interface WeatherResponse {
  location_id: string;
  updated_at: string;
  severity: number | null; // 1–5, null when live weather is unavailable
  severity_label: string | null;
  chance_pct: number | null;
  peak_day: "today" | "tomorrow" | null;
  reasons: Reason[];
  hourly: WeatherHour[];
  alerts: NwsAlert[] | null; // null = could not check
  fallback: boolean;
  fallback_reason: string | null;
  model: { auc_out_of_time: number; damage_days: number; days: number } | null;
}

// ---- Chat ----------------------------------------------------------------------

export interface ChatResponse {
  location_id: string;
  answer: string;
  source: "model" | "template"; // model = written by Bedrock, every number checked
  validated: boolean;
  suggestions: string[];
  meta: Meta;
}
