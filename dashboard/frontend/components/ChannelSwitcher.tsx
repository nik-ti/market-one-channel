// The channel switcher in the header. A plain <select> — same pattern as
// the source filter in PostsFeed — rather than a custom dropdown, so it
// stays keyboard/screen-reader friendly for free.
"use client";

import type { Channel } from "@/lib/types";

export function ChannelSwitcher({
  channel,
  channels,
  onChange,
}: {
  channel: string;
  channels: Channel[];
  onChange: (id: string) => void;
}) {
  // Before /channels has loaded, show just the id so the select always has
  // a matching option instead of silently falling back to the browser's
  // "first option" default.
  const options = channels.length > 0 ? channels : [{ id: channel, name: channel, ready: true }];

  return (
    <select
      value={channel}
      onChange={(e) => onChange(e.target.value)}
      aria-label="Channel"
      // py-3 gives a 44px tap target on phones; sm: restores the compact
      // desktop size, matching the other selects in this app.
      className="min-w-0 max-w-[9.5rem] truncate rounded-md border border-border bg-surface-primary px-2 py-3 text-sm text-ink-primary sm:max-w-[12rem] sm:py-1.5"
    >
      {options.map((c) => (
        <option key={c.id} value={c.id}>
          {c.name}
          {!c.ready ? " (no data yet)" : ""}
        </option>
      ))}
    </select>
  );
}
