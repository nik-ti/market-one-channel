// Shadcn/ui's standard class-merging helper: lets components accept a
// `className` override without Tailwind class order/specificity fights.
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// Matches the reason string nodes/dedup.py writes: "duplicate of item 18441
// (same event as a recent story)". The parenthetical is optional so this
// still matches if that suffix ever changes shape upstream.
const DUPLICATE_REASON_RE = /^duplicate of item (\d+)\s*(?:\((.*)\))?$/i;

export function parseDuplicateReason(
  reason: string
): { matchedId: number; detail: string | null } | null {
  const match = reason.match(DUPLICATE_REASON_RE);
  if (!match) return null;
  return { matchedId: Number(match[1]), detail: match[2] ?? null };
}
