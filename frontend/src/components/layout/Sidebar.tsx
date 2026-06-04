import { NavLink } from "react-router-dom";
import { LayoutDashboard, Radio, Activity } from "lucide-react";
import { cn } from "@/lib/utils/cn";

const NAV_ITEMS = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/streams", label: "Streams", icon: Radio, end: false },
];

/** Primary navigation rail. Feature routes are wired up in later commits. */
export function Sidebar() {
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-border-subtle bg-surface-50/60">
      <div className="flex h-16 items-center gap-2 px-5">
        <Activity className="h-5 w-5 text-accent" />
        <span className="font-mono text-sm font-semibold tracking-tight text-content-primary">
          OmniTrack
        </span>
      </div>
      <nav className="flex flex-col gap-1 px-3 py-2">
        {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                isActive
                  ? "bg-surface-200 text-content-primary"
                  : "text-content-secondary hover:bg-surface-100 hover:text-content-primary",
              )
            }
          >
            <Icon className="h-4 w-4" />
            {label}
          </NavLink>
        ))}
      </nav>
      <div className="mt-auto px-5 py-4 text-[10px] uppercase tracking-widest text-content-muted">
        AI Operations Platform
      </div>
    </aside>
  );
}
