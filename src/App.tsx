import { useState } from "react";
import { AssessmentProvider, useAssessment } from "./assessment";
import AboutMe from "./components/AboutMe";
import BottomNav from "./components/BottomNav";
import ZipPicker from "./components/ZipPicker";
import { LocationProvider } from "./location";
import { ProfileProvider } from "./profile";
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
  const { data, live } = useAssessment();
  const alertCount = (live?.alerts?.length ?? 0) + (data?.model_estimate.alert_eligible ? 1 : 0);

  return (
    <div className="app">
      {tab === "home" && <Home onOpenAlerts={() => setTab("alerts")} onOpenPrepare={() => setTab("storm")} />}
      {tab === "radar" && <Radar />}
      {tab === "alerts" && <Alerts />}
      {tab === "storm" && <Storm />}
      {tab === "chat" && <Chat />}

      <BottomNav active={tab} onChange={setTab} alertCount={alertCount} />

      {/* Opens by itself on a first visit; any screen can reopen it. */}
      <ZipPicker />
      <AboutMe />
    </div>
  );
}

export default function App() {
  return (
    <LocationProvider>
      <AssessmentProvider>
        <ProfileProvider>
          <AppShell />
        </ProfileProvider>
      </AssessmentProvider>
    </LocationProvider>
  );
}
