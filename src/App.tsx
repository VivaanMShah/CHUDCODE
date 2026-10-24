import { useEffect, useState } from "react";
import { fetchAlerts } from "api/placeholders";
import BottomNav from "./components/BottomNav";
import LocationPicker from "./components/LocationPicker";
import { LocationProvider, useLocation } from "./location";
import Alerts from "./screens/Alerts";
import Chat from "./screens/Chat";
import Home from "./screens/Home";
import Radar from "./screens/Radar";
import Storm from "./screens/Storm";
import type { TabId } from "./types";

// The shell lives inside <LocationProvider> so it can read the user's location.
function AppShell() {
  const { coords } = useLocation();
  const lat = coords?.lat;
  const lon = coords?.lon;

  const [tab, setTab] = useState<TabId>("home");
  const [alertCount, setAlertCount] = useState(0);

  // Load the alert count so the red badge on the nav is correct, and reload
  // it when the user changes location. TODO: also refresh on an interval, or
  // push updates from your backend (websocket / server-sent events).
  useEffect(() => {
    let cancelled = false;
    const c = lat !== undefined && lon !== undefined ? { lat, lon } : undefined;
    fetchAlerts(c)
      .then((d) => !cancelled && setAlertCount(d.alerts.length))
      .catch(() => {
        /* badge simply stays hidden if this fails */
      });
    return () => {
      cancelled = true;
    };
  }, [lat, lon]);

  return (
    <div className="app">
      {tab === "home" && <Home onOpenAlerts={() => setTab("alerts")} />}
      {tab === "radar" && <Radar />}
      {tab === "alerts" && <Alerts />}
      {tab === "storm" && <Storm />}
      {tab === "chat" && <Chat />}

      <BottomNav active={tab} onChange={setTab} alertCount={alertCount} />

      {/* Opens by itself on a first visit; any screen can reopen it. */}
      <LocationPicker />
    </div>
  );
}

export default function App() {
  return (
    <LocationProvider>
      <AppShell />
    </LocationProvider>
  );
}