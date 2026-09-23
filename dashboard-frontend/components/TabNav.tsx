// The 5 tabs. Switching tabs is local state in page.tsx — TabNav just
// renders the strip and reports clicks upward.
"use client";

import { cn } from "@/lib/utils";

export const TABS = ["Posts", "Stories", "Stats", "Graph", "Prompts"] as const;
export type Tab = (typeof TABS)[number];

export function TabNav({
  active,
  onChange,
}: {
  active: Tab;
  onChange: (tab: Tab) => void;
}) {
  return (
    <nav className="flex gap-1 overflow-x-auto border-b border-border px-4">
      {TABS.map((tab) => (
        <button
          key={tab}
          onClick={() => onChange(tab)}
          className={cn(
            "shrink-0 border-b-2 px-3 py-2 text-sm font-medium transition-colors",
            active === tab
              ? "border-ink-primary text-ink-primary"
              : "border-transparent text-ink-muted hover:text-ink-primary"
          )}
        >
          {tab}
        </button>
      ))}
    </nav>
  );
}
