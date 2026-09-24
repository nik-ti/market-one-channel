// Shown in place of a tab's content when the chosen channel's API responses
// come back with "ready": false — no database file yet (ai_news, today),
// not an error. Calm and specific about what's missing, not a red banner.
import { channelLabel } from "@/lib/channel";

export function EmptyState({ channel }: { channel: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-surface-primary px-6 py-14 text-center">
      <p className="text-sm font-medium text-ink-primary">No data yet for {channelLabel(channel)}</p>
      <p className="max-w-sm text-xs text-ink-muted">
        This channel doesn&apos;t have a database yet — its pipeline hasn&apos;t collected anything.
        Once it starts running, this tab fills in on its own.
      </p>
    </div>
  );
}
