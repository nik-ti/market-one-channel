// TanStack Query hooks. Every hook polls every 10s (SPEC.md: "5-10 seconds,
// 10s is cleaner") and keeps the previous page's data visible while a
// refetch is in flight, so the UI never flashes blank on a poll tick.
"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  fetchChannels,
  fetchGraph,
  fetchHealth,
  fetchNodes,
  fetchPosts,
  fetchStats,
  fetchStories,
  forcePublish,
  markShouldNotHavePosted,
} from "@/lib/api";
import type { PostFilters, StatsRange } from "@/lib/types";

export const POLL_INTERVAL_MS = 10_000;

export function usePosts(channel: string, filters: PostFilters, limit: number, offset: number) {
  return useQuery({
    queryKey: ["posts", channel, filters.source, filters.statuses, filters.q, limit, offset],
    queryFn: () => fetchPosts(channel, filters, limit, offset),
    refetchInterval: POLL_INTERVAL_MS,
    placeholderData: keepPreviousData,
  });
}

export function useStories(channel: string) {
  return useQuery({
    queryKey: ["stories", channel],
    queryFn: () => fetchStories(channel),
    refetchInterval: POLL_INTERVAL_MS,
    placeholderData: keepPreviousData,
  });
}

export function useStats(channel: string, range: StatsRange = "7d") {
  return useQuery({
    queryKey: ["stats", channel, range],
    queryFn: () => fetchStats(channel, range),
    refetchInterval: POLL_INTERVAL_MS,
    placeholderData: keepPreviousData,
  });
}

export function useGraph(channel: string) {
  return useQuery({
    queryKey: ["graph", channel],
    queryFn: () => fetchGraph(channel),
    refetchInterval: POLL_INTERVAL_MS,
    placeholderData: keepPreviousData,
  });
}

export function useNodes(channel: string) {
  return useQuery({
    queryKey: ["nodes", channel],
    queryFn: () => fetchNodes(channel),
    // Node prompts/models only change when someone edits source or .env —
    // no need to hammer the API for it every 10s.
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  });
}

// The channel list itself changes only when someone adds a channel folder or
// its pipeline runs for the first time — a slow poll is enough to notice
// "ai_news just got its first database" without hammering the API.
export function useChannels() {
  return useQuery({
    queryKey: ["channels"],
    queryFn: fetchChannels,
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  });
}

// Independent of whichever tab is open — drives the global "API offline"
// banner in Header/page.tsx.
export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: POLL_INTERVAL_MS,
    retry: 2,
  });
}

// Overrule / regret actions from the Posts tab. On success the feed and stats
// are refetched at once so the new status shows without waiting for a poll.
export function useItemAction(channel: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ kind, itemId, note }: { kind: "force" | "regret"; itemId: number; note?: string }) =>
      kind === "force"
        ? forcePublish(channel, itemId, note)
        : markShouldNotHavePosted(channel, itemId, note),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["posts", channel] });
      client.invalidateQueries({ queryKey: ["stats", channel] });
    },
  });
}
