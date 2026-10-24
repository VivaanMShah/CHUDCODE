import { useState } from "react";
import { AssessmentProvider, useAssessment } from "./assessment";
import BottomNav from "./components/BottomNav";
import LocationPicker from "./components/LocationPicker";
import { LocationProvider } from "./location";
import Alerts from "./screens/Alerts";
import Chat from "./screens/Chat";
import Home from "./screens/Home";
import Radar from "./screens/Radar";
import Storm from "./screens/Storm";
import type { TabId } from "./types";

// The shell lives inside both providers so every tab shares the same area,
// scenario, and backend assessment.
function AppShell() {
  const [tab, setTab] = useState<TabId>("home");
  const { data } = useAssessment();
  const alertCount = data?.model_estimate.alert_eligible ? 1 : 0;

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
      <AssessmentProvider>
        <AppShell />
      </AssessmentProvider>
    </LocationProvider>
  );
}
