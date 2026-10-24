// ============================================================================
// SAMPLE DATA: the chat replies are placeholders until the Bedrock chatbot is connected.
//
// The outage backend has no weather radar, evacuation zones, public place
// directory, or chatbot. These screens keep their design sample data so the
// app still shows them, and the UI labels them "Sample data".
// (Moved here from node_modules/api/placeholders.ts.)
// ============================================================================

import type {
  ChatMessage,
  ChatReply,
} from "../types";

const fakeDelay = (ms = 250) => new Promise((r) => setTimeout(r, ms));

export async function sendChatMessage(
  question: string,
  _location: { label: string } | null,
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