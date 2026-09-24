// Stories tab: every story, with every post/item inside it shown in full —
// title, body, status, and the AI node's reason for that status. Live
// stories first, closed ones tucked into a collapsed section so the tab
// stays scannable once the channel has history.
"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { StatusReason } from "@/components/StatusReason";
import { Badge, statusTone } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useStories } from "@/hooks/useApi";
import type { Story, StoryPost } from "@/lib/types";

function formatTime(iso: string | null) {
  if (!iso) return "none yet";
  const date = new Date((iso.includes("T") ? iso : iso.replace(" ", "T")) + "Z");
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

// Color-coded status marker per the dashboard's spec (🟢 published, ⚫
// merged, 🔴 rejected, 🟡 held).
const STATUS_MARK: Record<StoryPost["status"], string> = {
  published: "🟢",
  merged: "⚫",
  held: "🟡",
  rejected: "🔴",
};

function StoryPostRow({ post }: { post: StoryPost }) {
  const label = post.status === "published" ? "Post" : "Item";
  return (
    <div className="flex flex-col gap-1.5 border-t border-border py-3 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <span aria-hidden>{STATUS_MARK[post.status] ?? "⚪"}</span>
        <span className="text-sm font-medium text-ink-primary">
          {label} ({post.status})
        </span>
        <Badge tone={statusTone(post.status)}>{post.status}</Badge>
      </div>
      <p className="text-xs text-ink-muted">ID: {post.item_id}</p>
      <p className="text-sm font-medium text-ink-primary">{post.title}</p>
      {post.body && post.body !== post.title && (
        <p className="whitespace-pre-wrap text-sm text-ink-muted">{post.body}</p>
      )}
      {post.status_reason && (
        <p className="text-xs text-ink-muted">
          → <StatusReason reason={post.status_reason} />
        </p>
      )}
    </div>
  );
}

function StoryCard({ story }: { story: Story }) {
  const [open, setOpen] = useState(false);

  return (
    <Card>
      <CardHeader>
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-start justify-between gap-3 text-left"
        >
          <div className="flex flex-col gap-1">
            <span className="text-xs text-ink-muted">
              Story #{story.id} &middot; {story.state}
            </span>
            <CardTitle>{story.headline}</CardTitle>
            {story.summary && <p className="text-sm text-ink-muted">{story.summary}</p>}
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted">
              <span>{story.item_count} items</span>
              <span>{story.post_count} posts</span>
              <span>Last post: {formatTime(story.last_post_at)}</span>
            </div>
          </div>
          {open ? (
            <ChevronDown className="mt-1 h-4 w-4 shrink-0 text-ink-muted" />
          ) : (
            <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-ink-muted" />
          )}
        </button>
      </CardHeader>
      {open && (
        <CardContent className="pt-0">
          {story.posts.length === 0 ? (
            <p className="text-sm text-ink-muted">No items in this story yet.</p>
          ) : (
            <div className="flex flex-col">
              {story.posts.map((post) => (
                <StoryPostRow key={post.item_id} post={post} />
              ))}
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}

export function StoriesView({ channel }: { channel: string }) {
  const { data, isFetching } = useStories(channel);
  const [showClosed, setShowClosed] = useState(false);

  if (data && !data.ready) {
    return (
      <div className="p-4">
        <EmptyState channel={channel} />
      </div>
    );
  }

  const live = data?.stories.filter((s) => s.state === "live") ?? [];
  const closed = data?.stories.filter((s) => s.state !== "live") ?? [];

  return (
    <div className="flex flex-col gap-6 p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink-primary">Active stories ({live.length})</h2>
        {isFetching && <span className="text-xs text-ink-muted">Refreshing...</span>}
      </div>

      <div className="grid grid-cols-1 gap-3">
        {live.map((story) => (
          <StoryCard key={story.id} story={story} />
        ))}
        {live.length === 0 && <p className="text-sm text-ink-muted">No active stories right now.</p>}
      </div>

      <div>
        <button
          onClick={() => setShowClosed((v) => !v)}
          className="flex min-h-[44px] w-full items-center gap-1.5 text-sm font-semibold text-ink-primary"
        >
          {showClosed ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          Closed stories ({closed.length})
        </button>
        {showClosed && (
          <div className="mt-3 grid grid-cols-1 gap-3">
            {closed.map((story) => (
              <StoryCard key={story.id} story={story} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
