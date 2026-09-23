// Every fetch call the app makes. The base URL always comes from the
// environment (never hardcoded — see SPEC.md FAILURE #10) so the same build
// works against localhost in dev and the VPS through Vercel in prod.
import type {
  GraphResponse,
  PostsResponse,
  PromptsResponse,
  StatsResponse,
  StoriesResponse,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

if (!API_URL) {
  // Loud in the browser console during dev if the env var was never set,
  // instead of every request silently failing against a blank URL.
  console.error(
    "NEXT_PUBLIC_API_URL is not set. Create .env.local from .env.example."
  );
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export function fetchPosts(source: string | null, limit: number, offset: number) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (source) params.set("source", source);
  return get<PostsResponse>(`/posts?${params.toString()}`);
}

export function fetchStories() {
  return get<StoriesResponse>("/stories");
}

export function fetchStats() {
  return get<StatsResponse>("/stats");
}

export function fetchGraph() {
  return get<GraphResponse>("/graph");
}

export function fetchPrompts() {
  return get<PromptsResponse>("/prompts");
}

// /health is not under /api/v1 in api.ts's base path sense — it lives at
// the API root's /api/v1/health, which NEXT_PUBLIC_API_URL already points
// under (see .env.example), so this reuses the same base.
export function fetchHealth() {
  return get<{ status: string }>("/health");
}
