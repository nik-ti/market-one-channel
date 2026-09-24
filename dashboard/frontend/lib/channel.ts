// Shared channel constants/helpers — kept separate from types.ts so both
// the URL-state hook in page.tsx and any tab component can import just this,
// without pulling in every response shape.

export const DEFAULT_CHANNEL = "markets";

// A human label built the same way the backend's channel_resolver falls
// back when a channel's own display name (from /channels) isn't loaded yet
// — e.g. "ai_news" -> "AI News". Used only until the real list arrives.
export function channelLabel(id: string): string {
  return id
    .split(/[_-]+/)
    .filter(Boolean)
    .map((w) => (w.toLowerCase() === "ai" ? "AI" : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(" ");
}
