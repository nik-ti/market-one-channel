// Status pill used by the Posts table and Stories cards. Colors map 1:1 to
// SPEC.md's status color table.
import { cn } from "@/lib/utils";

export type BadgeTone =
  | "published"
  | "rejected"
  | "hold"
  | "duplicate"
  | "expired"
  | "merged"
  | "neutral";

const TONE_CLASSES: Record<BadgeTone, string> = {
  published: "bg-status-published/10 text-status-published",
  rejected: "bg-status-rejected/10 text-status-rejected",
  hold: "bg-status-hold/10 text-status-hold",
  duplicate: "bg-status-duplicate/10 text-status-duplicate",
  expired: "bg-status-expired/10 text-status-expired",
  // Merged items didn't fail or get held — they were folded into another
  // post. Same neutral gray as "expired" (nothing more to see here).
  merged: "bg-status-expired/10 text-status-expired",
  neutral: "bg-ink-muted/10 text-ink-muted",
};

export function Badge({ tone, children }: { tone: BadgeTone; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
        TONE_CLASSES[tone]
      )}
    >
      {children}
    </span>
  );
}

// Maps the many raw `items.status` values from the DB down to the 5 colors
// SPEC.md defines. Anything not explicitly a "bad" outcome or one of the
// spec's named states falls back to neutral rather than guessing.
const STATUS_TONE: Record<string, BadgeTone> = {
  published: "published",
  held: "hold",
  duplicate: "duplicate",
  expired: "expired",
  irrelevant: "rejected",
  low_impact: "rejected",
  failed: "rejected",
  // The /stories endpoint pre-buckets everything that isn't
  // published/merged/held into the literal string "rejected".
  rejected: "rejected",
  merged: "merged",
  skipped_stale: "expired",
  skipped_backlog: "expired",
  skipped_handle: "expired",
};

export function statusTone(status: string): BadgeTone {
  return STATUS_TONE[status] ?? "neutral";
}
