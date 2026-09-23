// Renders an items.status_reason string, highlighting the "duplicate of
// item N" part the dedup node writes (nodes/dedup.py) as a pill so a reader
// can spot at a glance which earlier item a duplicate matches. Older rows
// written before that format existed just render as plain text.
import { parseDuplicateReason } from "@/lib/utils";

export function StatusReason({ reason }: { reason: string }) {
  if (!reason) return null;

  const dup = parseDuplicateReason(reason);
  if (!dup) {
    return <span>{reason}</span>;
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <span className="inline-flex items-center rounded-full bg-status-duplicate/15 px-2 py-0.5 text-xs font-semibold text-status-duplicate">
        duplicate of item {dup.matchedId}
      </span>
      {dup.detail && <span>({dup.detail})</span>}
    </span>
  );
}
