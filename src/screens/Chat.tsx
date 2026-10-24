import { useEffect, useRef, useState, type FormEvent } from "react";
import { sendChatMessage } from "../api/samples";
import { useLocation } from "../location";
import type { ChatMessage } from "../types";
import "./Chat.css";

const MAX_LENGTH = 500;

const STARTER_QUESTIONS = [
  "How bad will it get?",
  "Should I evacuate?",
  "Where is the nearest shelter?",
  "What should I pack?",
];

function timeNow(): string {
  return new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export default function Chat() {
  const { location } = useLocation();

  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "welcome",
      role: "assistant",
      text: "Hi! I can answer questions about the weather near you: how bad a storm might get, whether to evacuate, where the closest shelter or hospital is, and how to prepare. What would you like to know?",
      sentAt: timeNow(),
    },
  ]);
  const [suggestions, setSuggestions] = useState<string[]>(STARTER_QUESTIONS);
  const [draft, setDraft] = useState("");
  const [waiting, setWaiting] = useState(false);

  const listRef = useRef<HTMLDivElement>(null);
  const idCounter = useRef(0);
  const nextId = () => `m${++idCounter.current}`;

  // Keep the newest message in view.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages, waiting]);

  async function ask(rawQuestion: string) {
    const question = rawQuestion.trim();
    if (!question || waiting) return;

    const userMessage: ChatMessage = {
      id: nextId(),
      role: "user",
      text: question,
      sentAt: timeNow(),
    };
    const history = [...messages, userMessage];

    setMessages(history);
    setSuggestions([]);
    setDraft("");
    setWaiting(true);

    try {
      const reply = await sendChatMessage(question, location, history);
      setMessages((m) => [
        ...m,
        { id: nextId(), role: "assistant", text: reply.text, sentAt: timeNow() },
      ]);
      setSuggestions(reply.suggestions ?? []);
    } catch {
      setMessages((m) => [
        ...m,
        {
          id: nextId(),
          role: "assistant",
          text: "Sorry, I couldn't get an answer just now. Please try again. If this is an emergency, call 911.",
          sentAt: timeNow(),
        },
      ]);
      setSuggestions([question]); // lets the user retry with one tap
    } finally {
      setWaiting(false);
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    void ask(draft);
  }

  return (
    <div className="chat">
      <header className="chat__header">
        <div>
          <h1>Weather assistant</h1>
          <p>Sample replies · not connected to the backend yet</p>
        </div>
      </header>

      <div
        className="chat__messages"
        ref={listRef}
        role="log"
        aria-live="polite"
        aria-label="Conversation"
      >
        {messages.map((m) => (
          <div key={m.id} className={`bubble-row bubble-row--${m.role}`}>
            <div className={`bubble bubble--${m.role}`}>
              <p>{m.text}</p>
              <span className="bubble__time">{m.sentAt}</span>
            </div>
          </div>
        ))}

        {waiting && (
          <div className="bubble-row bubble-row--assistant">
            <div className="bubble bubble--assistant bubble--typing" aria-label="Assistant is typing">
              <span className="typing-dot" />
              <span className="typing-dot" />
              <span className="typing-dot" />
            </div>
          </div>
        )}

        {/* Tappable follow-up questions */}
        {!waiting && suggestions.length > 0 && (
          <div className="chat__chips">
            {suggestions.map((s) => (
              <button
                key={s}
                type="button"
                className="chip"
                onClick={() => void ask(s)}
              >
                {s}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="chat__footer">
        <form className="chat__composer" onSubmit={handleSubmit}>
          <input
            type="text"
            value={draft}
            onChange={(e) => setDraft(e.target.value.slice(0, MAX_LENGTH))}
            placeholder="Ask about the weather…"
            aria-label="Your question"
            autoComplete="off"
            enterKeyHint="send"
          />
          <button
            type="submit"
            className="chat__send"
            disabled={waiting || draft.trim().length === 0}
            aria-label="Send"
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden
            >
              <path d="M12 19V5M5 12l7-7 7 7" />
            </svg>
          </button>
        </form>
        <p className="chat__disclaimer">
          Not a substitute for official emergency instructions. In an emergency, call 911.
        </p>
      </div>
    </div>
  );
}