import { useEffect, useRef, useState } from "react";
import { getMessage } from "../api/backend";
import type { MessageType } from "../api/contract";
import { useAssessment } from "../assessment";
import { useLocation } from "../location";

// A demo of text-message alerts. It is a pretend sign-up: nothing is sent, and the number you type
// stays in this component's memory only (not saved, not sent to any server).

const STAGES: { id: MessageType; label: string }[] = [
  { id: "pre_storm", label: "Before the storm" },
  { id: "outage_confirmed", label: "Outage confirmed" },
  { id: "estimate_updated", label: "Estimate updated" },
  { id: "restoration_complete", label: "Power restored" },
];

interface Bubble {
  id: string;
  from: "app" | "me";
  text: string;
  label?: string;
  time: string;
}

const now = () => new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function formatPhone(d: string): string {
  const a = d.slice(0, 3), b = d.slice(3, 6), c = d.slice(6, 10);
  if (d.length > 6) return `(${a}) ${b}-${c}`;
  if (d.length > 3) return `(${a}) ${b}`;
  return a;
}

export default function SmsDemo() {
  const { location } = useLocation();
  const { scenario, quiet } = useAssessment();
  const [digits, setDigits] = useState("");
  const [consent, setConsent] = useState(false);
  const [on, setOn] = useState(false);
  const [thread, setThread] = useState<Bubble[]>([]);
  const [typing, setTyping] = useState(false);
  const run = useRef(0); // lets a new replay cancel an old one
  const listRef = useRef<HTMLDivElement>(null);
  const counter = useRef(0);

  useEffect(() => () => void run.current++, []);
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [thread, typing]);

  const push = (b: Omit<Bubble, "id" | "time">) =>
    setThread((t) => [...t, { ...b, id: `b${++counter.current}`, time: now() }]);

  async function play() {
    if (!location) return;
    const id = ++run.current;
    setOn(true);
    setThread([]);
    push({ from: "app", text: `Outage Concierge: You're signed up for outage texts for ZIP ${location.zip}. Msg and data rates may apply. Reply STOP to cancel.` });
    const msgs = await Promise.all(
      STAGES.map((s) => getMessage(location.locationId, s.id, scenario).catch(() => null)),
    );
    for (let i = 0; i < STAGES.length; i++) {
      setTyping(true);
      await sleep(1300);
      if (run.current !== id) return;
      setTyping(false);
      const m = msgs[i];
      push({ from: "app", label: STAGES[i].label, text: m ? `Outage Concierge: ${m.text}` : "Outage Concierge: We couldn't load this message right now." });
    }
  }

  function stop() {
    run.current++;
    setTyping(false);
    push({ from: "me", text: "STOP" });
    push({ from: "app", text: "Outage Concierge: You're unsubscribed. No more texts will be sent." });
    setTimeout(() => {
      setOn(false);
      setThread([]);
      setDigits("");
      setConsent(false);
    }, 2200);
  }

  const valid = digits.length === 10;

  return (
    <section className="card sms" aria-label="Text alerts demo">
      <div className="sms__top">
        <span className="eyebrow">Text message preview</span>
        <span className="sms__demo">Demo</span>
      </div>

      {!on ? (
        <form
          className="sms__form"
          onSubmit={(e) => {
            e.preventDefault();
            if (valid && consent) void play();
          }}
        >
          <p className="sms__lead">See what a text alert would look like.</p>
          <label className="sms__field">
            <span>Mobile number</span>
            <input
              type="tel"
              inputMode="tel"
              autoComplete="off"
              placeholder="(555) 555-0100"
              value={formatPhone(digits)}
              onChange={(e) => setDigits(e.target.value.replace(/\D/g, "").slice(0, 10))}
              aria-describedby="sms-note"
            />
          </label>
          <label className="sms__consent">
            <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            <span>I agree to get outage texts for ZIP {location?.zip}. Reply STOP anytime.</span>
          </label>
          <button type="submit" className="sms__go" disabled={!valid || !consent}>
            Turn on text alerts
          </button>
          <p id="sms-note" className="sms__note">
            Preview only. Real texts need a registered sending number, so use Phone alerts above for real notifications. No texts are sent, and your number is not saved or sent anywhere. Any fake number works.
          </p>
        </form>
      ) : (
        <div className="sms__phone">
          <div className="sms__bar">
            <strong>Outage Concierge</strong>
            <small>To: ({digits.slice(0, 3).replace(/./g, "•")}) {digits.slice(3, 6).replace(/./g, "•")}-{digits.slice(6)}</small>
          </div>
          {quiet && <p className="sms__quiet">No storm now. These are previews of what we'd send if one is forecast.</p>}
          <div className="sms__thread" ref={listRef} role="log" aria-live="polite">
            {thread.map((b) => (
              <div key={b.id} className={`sms__row sms__row--${b.from}`}>
                {b.label && <span className="sms__stage">{b.label}</span>}
                <div className={`sms__bubble sms__bubble--${b.from}`}>{b.text}</div>
                <span className="sms__time">{b.time}</span>
              </div>
            ))}
            {typing && (
              <div className="sms__row sms__row--app">
                <div className="sms__bubble sms__bubble--app sms__typing" aria-label="Typing">
                  <i /><i /><i />
                </div>
              </div>
            )}
          </div>
          <div className="sms__actions">
            <button type="button" className="link" onClick={() => void play()}>Replay</button>
            <button type="button" className="link" onClick={stop}>Reply STOP</button>
          </div>
        </div>
      )}
    </section>
  );
}
