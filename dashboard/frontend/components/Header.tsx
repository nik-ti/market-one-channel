// Sticky top bar: logo, channel switcher, theme toggle, and a help icon.
// Light mode is the default on every fresh visit; the theme choice made here
// only sticks around in this browser via localStorage. The channel choice is
// lifted to the URL by the caller (page.tsx) so a link can be shared.
"use client";

import { Info, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { ChannelSwitcher } from "@/components/ChannelSwitcher";
import { Button } from "@/components/ui/button";
import type { Channel } from "@/lib/types";

const THEME_KEY = "market-one-theme";

export function Header({
  channel,
  channels,
  onChannelChange,
}: {
  channel: string;
  channels: Channel[];
  onChannelChange: (id: string) => void;
}) {
  const [dark, setDark] = useState(false);
  const [showHelp, setShowHelp] = useState(false);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(THEME_KEY);
      if (saved === "dark") setDark(true);
    } catch {
      // Storage can be unavailable (private browsing); light mode is fine.
    }
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem(THEME_KEY, dark ? "dark" : "light");
    } catch {
      // Non-fatal — the toggle still works for this page view.
    }
  }, [dark]);

  return (
    <header className="sticky top-0 z-20 flex flex-wrap items-center justify-between gap-2 border-b border-border bg-surface-primary px-4 py-3">
      <div className="flex min-w-0 items-center gap-2">
        <span className="shrink-0 text-base font-semibold text-ink-primary">Simple Flow Channels</span>
        <ChannelSwitcher channel={channel} channels={channels} onChange={onChannelChange} />
      </div>

      <div className="relative flex items-center gap-1">
        <Button
          variant="ghost"
          size="icon"
          aria-label="About this dashboard"
          onClick={() => setShowHelp((v) => !v)}
        >
          <Info className="h-4 w-4" />
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
          onClick={() => setDark((v) => !v)}
        >
          {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>

        {showHelp && (
          <div className="absolute right-0 top-10 w-64 rounded-md border border-border bg-surface-primary p-3 text-xs text-ink-muted shadow-md">
            Live monitor over the Simple Flow news channels. Pick a channel above;
            data refreshes every 10 seconds from that channel&apos;s own database.
          </div>
        )}
      </div>
    </header>
  );
}
