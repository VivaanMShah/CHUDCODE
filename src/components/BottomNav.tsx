import type { ReactElement } from "react";
import type { TabId } from "../types";
import "./BottomNav.css";

interface BottomNavProps {
  active: TabId;
  onChange: (tab: TabId) => void;
  alertCount: number; // red badge on the Alerts tab (hidden when 0)
}

// Small stroke icons, drawn inline so we need no icon library.
const iconProps = {
  width: 22,
  height: 22,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.8,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  "aria-hidden": true,
};

const icons: Record<TabId, ReactElement> = {
  home: (
    <svg {...iconProps}>
      <path d="M3 11.5 12 4l9 7.5" />
      <path d="M5.5 10v9.5h13V10" />
      <path d="M10 19.5v-5h4v5" />
    </svg>
  ),
  radar: (
    <svg {...iconProps}>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="1.2" fill="currentColor" />
    </svg>
  ),
  alerts: (
    <svg {...iconProps}>
      <path d="M12 3.5 2.8 19.5h18.4L12 3.5Z" />
      <path d="M12 10v4.5" />
      <circle cx="12" cy="17" r="0.6" fill="currentColor" />
    </svg>
  ),
  storm: (
    <svg {...iconProps}>
      <path d="m12 3 8 17-8-4-8 4 8-17Z" />
    </svg>
  ),
  chat: (
    <svg {...iconProps}>
      <path d="M20.5 11.5a7.5 7.5 0 0 1-10.9 6.7L4 19.5l1.4-4.3A7.5 7.5 0 1 1 20.5 11.5Z" />
      <path d="M8.5 11.5h.01M12 11.5h.01M15.5 11.5h.01" strokeWidth="2.4" />
    </svg>
  ),
};

const tabs: { id: TabId; label: string }[] = [
  { id: "home", label: "Home" },
  { id: "radar", label: "Radar" },
  { id: "alerts", label: "Alerts" },
  { id: "storm", label: "Storm" },
  { id: "chat", label: "Chat" },
];

export default function BottomNav({
  active,
  onChange,
  alertCount,
}: BottomNavProps) {
  return (
    <nav className="bottom-nav" aria-label="Primary">
      {tabs.map((tab) => {
        const isActive = tab.id === active;
        return (
          <button
            key={tab.id}
            type="button"
            className={`bottom-nav__item${isActive ? " is-active" : ""}`}
            onClick={() => onChange(tab.id)}
            aria-current={isActive ? "page" : undefined}
          >
            <span className="bottom-nav__icon">
              {icons[tab.id]}
              {tab.id === "alerts" && alertCount > 0 && (
                <span
                  className="bottom-nav__badge"
                  aria-label={`${alertCount} active alerts`}
                >
                  {alertCount}
                </span>
              )}
            </span>
            <span className="bottom-nav__label">{tab.label}</span>
          </button>
        );
      })}
    </nav>
  );
}