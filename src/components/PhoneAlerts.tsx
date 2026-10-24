import { useState } from "react";
import { getAlertConfig, subscribePush, testPush, unsubscribePush } from "../api/backend";
import { useLocation } from "../location";

// Real phone alerts. They arrive as notifications on the lock screen, like a text message.
// (Real SMS needs a carrier-registered sending number, which this app does not have yet.)
// We keep only the phone's push address and the ZIP, and delete both when alerts are turned off.

const KEY = "storm-watch:push-id";

function b64ToBytes(b64: string): Uint8Array<ArrayBuffer> {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4);
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function readId(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function support(): "ok" | "install" | "unsupported" {
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const standalone = window.matchMedia("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true;
  if (ios && !standalone) return "install"; // iPhones only allow alerts for sites added to the home screen
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window ? "ok" : "unsupported";
}

export default function PhoneAlerts() {
  const { location } = useLocation();
  const [id, setId] = useState<string | null>(readId);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const mode = support();

  async function turnOn() {
    if (!location) return;
    setBusy(true);
    setMessage(null);
    try {
      const cfg = await getAlertConfig();
      if ((await Notification.requestPermission()) !== "granted") {
        setMessage("Notifications are blocked. Allow them for this site in your browser settings, then try again.");
        return;
      }
      const reg = await navigator.serviceWorker.register("/sw.js");
      await navigator.serviceWorker.ready;
      const sub =
        (await reg.pushManager.getSubscription()) ??
        (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(cfg.public_key) }));
      const r = await subscribePush(sub.toJSON(), location.zip);
      try {
        localStorage.setItem(KEY, r.id);
      } catch {
        /* not critical */
      }
      setId(r.id);
      setMessage("Phone alerts are on. Tap 'Send me a test alert' to see one.");
    } catch {
      setMessage("Couldn't turn on alerts. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  }

  async function test() {
    if (!id) return;
    setBusy(true);
    try {
      const r = await testPush(id);
      setMessage(r.detail);
      if (!r.sent && r.detail.includes("turned alerts off")) turnOff(false);
    } catch {
      setMessage("Couldn't send the test. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function turnOff(tellServer = true) {
    try {
      if (tellServer && id) await unsubscribePush(id);
      const reg = await navigator.serviceWorker.getRegistration("/sw.js");
      await (await reg?.pushManager.getSubscription())?.unsubscribe();
    } catch {
      /* the server entry also expires by itself */
    }
    try {
      localStorage.removeItem(KEY);
    } catch {
      /* not critical */
    }
    setId(null);
    setMessage("Phone alerts are off and your push address was deleted.");
  }

  return (
    <section className="card phone" aria-label="Phone alerts">
      <span className="eyebrow">Phone alerts</span>
      <p className="phone__lead">
        Get a notification on your lock screen, like a text, when a storm or a National Weather Service alert is coming for ZIP{" "}
        {location?.zip}.
      </p>

      {mode === "install" && (
        <ol className="phone__steps">
          <li>Tap the Share button in Safari.</li>
          <li>Tap "Add to Home Screen".</li>
          <li>Open Storm Watch from your home screen, then come back here.</li>
        </ol>
      )}
      {mode === "unsupported" && <p className="phone__note">This browser can't show phone alerts. Try Chrome or Safari on your phone.</p>}

      {mode === "ok" &&
        (id ? (
          <div className="phone__row">
            <button type="button" className="sms__go" onClick={() => void test()} disabled={busy}>Send me a test alert</button>
            <button type="button" className="link" onClick={() => void turnOff()} disabled={busy}>Turn off</button>
          </div>
        ) : (
          <button type="button" className="sms__go" onClick={() => void turnOn()} disabled={busy || !location}>
            {busy ? "Turning on…" : "Turn on phone alerts"}
          </button>
        ))}

      {message && <p className="phone__msg" role="status">{message}</p>}
      <p className="phone__note">
        We store only your phone's push address and your ZIP, and delete them when you turn alerts off (or after 30 days). No
        name and no phone number.
      </p>
    </section>
  );
}
