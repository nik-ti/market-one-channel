// Sticky top bar: logo, theme toggle, and a help icon. Light mode is the
// default on every fresh visit; the choice made here only sticks around in
// this browser via localStorage.
"use client";

import { Info, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";

const THEME_KEY = "market-one-theme";

export function Header() {
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
    <header className="sticky top-0 z-20 flex items-center justify-between border-b border-border bg-surface-primary px-4 py-3">
      <span className="text-base font-semibold text-ink-primary">Market One</span>

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
            Live monitor for the @market_one_news channel. Data refreshes
            every 10 seconds from the pipeline&apos;s database.
          </div>
        )}
      </div>
    </header>
  );
}
