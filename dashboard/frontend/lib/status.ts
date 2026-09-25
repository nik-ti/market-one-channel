// One place that knows what every raw `items.status` means to a reader: its
// label, a one-line explanation, its colour, and which filter chip it falls
// under. The Posts filters, the badges and the Stats breakdown all read this,
// so "low_impact" is called the same thing everywhere.

export interface StatusInfo {
  label: string;
  hint: string;
  // Hex, not a Tailwind class: recharts needs a real colour for SVG fills.
  color: string;
}

// Reserved outcome colours. Green/amber/red keep their usual meaning; the
// rest are deliberately quieter so the three that matter stand out.
export const STATUS_INFO: Record<string, StatusInfo> = {
  published: { label: "Published", hint: "Went out to the channel", color: "#10B981" },
  held: { label: "Held", hint: "Joined a story that had not moved yet — fuel for its next post", color: "#F59E0B" },
  queued: { label: "Queued", hint: "Waiting to be processed", color: "#6366F1" },
  written: { label: "Written", hint: "A post exists, waiting on the editor or on room to send", color: "#8B5CF6" },
  low_impact: { label: "Low impact", hint: "Real news, but nothing has to reprice on it", color: "#F97316" },
  irrelevant: { label: "Irrelevant", hint: "Not our topics, or not really news", color: "#DC2626" },
  duplicate: { label: "Duplicate", hint: "Already covered", color: "#2563EB" },
  merged: { label: "Merged", hint: "Folded into another item's story post", color: "#64748B" },
  expired: { label: "Expired", hint: "Sat in the queue too long and went stale", color: "#9CA3AF" },
  failed: { label: "Failed", hint: "Something broke repeatedly — see the reason", color: "#BE123C" },
  skipped_stale: { label: "Skipped · stale", hint: "Already too old when it was read", color: "#A8A29E" },
  skipped_backlog: { label: "Skipped · backlog", hint: "Too many arrived at once", color: "#A8A29E" },
  skipped_handle: { label: "Skipped · handle", hint: "From an X account this channel does not follow", color: "#A8A29E" },
};

const FALLBACK: StatusInfo = { label: "", hint: "", color: "#94A3B8" };

export function statusInfo(status: string): StatusInfo {
  const info = STATUS_INFO[status];
  if (info) return info;
  return { ...FALLBACK, label: status.replace(/_/g, " ") };
}

// The chips on the Posts tab. Each maps to one or more raw statuses; the
// three skip reasons and the two in-flight states are one chip each because
// nobody filters for "skipped_backlog but not skipped_stale".
export interface StatusFilter {
  id: string;
  label: string;
  statuses: string[];
  color: string;
}

export const STATUS_FILTERS: StatusFilter[] = [
  { id: "published", label: "Published", statuses: ["published"], color: STATUS_INFO.published.color },
  { id: "held", label: "Held", statuses: ["held"], color: STATUS_INFO.held.color },
  { id: "in_progress", label: "In progress", statuses: ["queued", "written"], color: STATUS_INFO.queued.color },
  { id: "low_impact", label: "Low impact", statuses: ["low_impact"], color: STATUS_INFO.low_impact.color },
  { id: "irrelevant", label: "Irrelevant", statuses: ["irrelevant"], color: STATUS_INFO.irrelevant.color },
  { id: "duplicate", label: "Duplicate", statuses: ["duplicate"], color: STATUS_INFO.duplicate.color },
  { id: "merged", label: "Merged", statuses: ["merged"], color: STATUS_INFO.merged.color },
  { id: "expired", label: "Expired", statuses: ["expired"], color: STATUS_INFO.expired.color },
  {
    id: "skipped",
    label: "Skipped",
    statuses: ["skipped_stale", "skipped_backlog", "skipped_handle"],
    color: STATUS_INFO.skipped_stale.color,
  },
  { id: "failed", label: "Failed", statuses: ["failed"], color: STATUS_INFO.failed.color },
];

// Statuses a human may overrule into the queue (backend refuses published).
// In-flight items are left alone: the pipeline has not decided yet.
export const FORCEABLE = new Set([
  "held",
  "low_impact",
  "irrelevant",
  "duplicate",
  "merged",
  "expired",
  "failed",
  "skipped_stale",
  "skipped_backlog",
  "skipped_handle",
]);
