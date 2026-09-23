// TanStack Query hooks. Every hook polls every 10s (SPEC.md: "5-10 seconds,
// 10s is cleaner") and keeps the previous page's data visible while a
// refetch is in flight, so the UI never flashes blank on a poll tick.
"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";

import {
  fetchGraph,
  fetchHealth,
  fetchPosts,
  fetchPrompts,
  fetchStats,
  fetchStories,
} from "@/lib/api";

export const POLL_INTERVAL_MS = 10_000;

export function usePosts(source: string | null, limit: number, offset: number) {
  return useQuery({
    queryKey: ["posts", source, limit, offset],
    queryFn: () => fetchPosts(source, limit, offset),
    refetchInterval: POLL_INTERVAL_MS,
    placeholderData: keepPreviousData,
  });
}

export function useStories() {
  return useQuery({
    queryKey: ["stories"],
    queryFn: fetchStories,
    refetchInterval: POLL_INTERVAL_MS,
    placeholderData: keepPreviousData,
  });
}

export function useStats() {
  return useQuery({
    queryKey: ["stats"],
    queryFn: fetchStats,
    refetchInterval: POLL_INTERVAL_MS,
    placeholderData: keepPreviousData,
  });
}

export function useGraph() {
  return useQuery({
    queryKey: ["graph"],
    queryFn: fetchGraph,
    refetchInterval: POLL_INTERVAL_MS,
    placeholderData: keepPreviousData,
  });
}

export function usePrompts() {
  return useQuery({
    queryKey: ["prompts"],
    queryFn: fetchPrompts,
    // Prompts only change when someone edits a node's source file — no
    // need to hammer the API for it every 10s.
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
