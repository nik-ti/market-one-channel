// Posts tab (default tab): filterable, paginated feed of everything the
// pipeline has ingested. Polls every 10s via usePosts().
//
// Two layouts share one expand/collapse state: a table from sm: up (a mouse
// and a wide screen make a dense grid the fastest way to scan), and a card
// per post below that (a 7-column table at 375px pushed Title/Story/Status
// off the right edge with no visible hint it could scroll).
"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useState } from "react";

import { StatusReason } from "@/components/StatusReason";
import { Badge, statusTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { usePosts, useStats } from "@/hooks/useApi";
import type { PostItem } from "@/lib/types";

const PAGE_SIZE = 50;

function formatTime(iso: string | null) {
  if (!iso) return "-";
  // DB times are stored as naive UTC strings ("YYYY-MM-DD HH:MM:SS").
  const withZone = iso.includes("T") ? iso : `${iso.replace(" ", "T")}Z`;
  const date = new Date(withZone);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

function PostCard({
  item,
  isOpen,
  onToggle,
}: {
  item: PostItem;
  isOpen: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface-primary">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={isOpen}
        aria-label={isOpen ? "Collapse full post" : "View full post"}
        className="flex min-h-[44px] w-full items-start justify-between gap-3 p-4 text-left"
      >
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-muted">
            <span>#{item.id}</span>
            <span aria-hidden>&middot;</span>
            <span>{formatTime(item.time)}</span>
          </div>
          <p className="text-sm font-medium text-ink-primary">{item.title}</p>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-muted">
            <span>{item.source_name}</span>
            {item.story_id != null && (
              <>
                <span aria-hidden>&middot;</span>
                <span>Story #{item.story_id}</span>
              </>
            )}
          </div>
          <Badge tone={statusTone(item.status)}>{item.status}</Badge>
        </div>
        {isOpen ? (
          <ChevronDown className="mt-1 h-5 w-5 shrink-0 text-ink-muted" />
        ) : (
          <ChevronRight className="mt-1 h-5 w-5 shrink-0 text-ink-muted" />
        )}
      </button>
      {isOpen && (
        <div className="border-t border-border p-4 pt-3">
          <p className="whitespace-pre-wrap text-sm text-ink-primary">
            {item.body || "(no body text)"}
          </p>
          {item.status_reason && (
            <p className="mt-2 text-xs text-ink-muted">
              → <StatusReason reason={item.status_reason} />
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export function PostsFeed() {
  const [source, setSource] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const { data: stats } = useStats();
  const { data, isFetching, dataUpdatedAt } = usePosts(source, PAGE_SIZE, page * PAGE_SIZE);

  function toggleExpanded(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const sources = stats?.sources_count.map((s) => s.source_name) ?? [];
  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <select
          value={source ?? ""}
          onChange={(e) => {
            setSource(e.target.value || null);
            setPage(0);
          }}
          // py-3 gives a 44px-tall tap target on phones; sm: restores the
          // original compact size once a mouse is doing the pointing.
          className="rounded-md border border-border bg-surface-primary px-3 py-3 text-sm text-ink-primary sm:py-1.5"
        >
          <option value="">All sources</option>
          {sources.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>

        <span className="text-xs text-ink-muted">
          {isFetching ? "Refreshing..." : `Last updated ${new Date(dataUpdatedAt).toLocaleTimeString()}`}
        </span>
      </div>

      {/* Cards below sm: — a 7-column table at phone width pushed Title,
          Story and Status off the right edge with no visible scroll hint. */}
      <div className="flex flex-col gap-3 sm:hidden">
        {data?.items.map((item) => (
          <PostCard
            key={item.id}
            item={item}
            isOpen={expanded.has(item.id)}
            onToggle={() => toggleExpanded(item.id)}
          />
        ))}
        {data?.items.length === 0 && (
          <p className="py-8 text-center text-sm text-ink-muted">No posts for this filter.</p>
        )}
      </div>

      <div className="table-scroll hidden rounded-lg border border-border bg-surface-primary sm:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>ID</TableHead>
              <TableHead>Time</TableHead>
              <TableHead>Source</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Story</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data?.items.map((item) => {
              const isOpen = expanded.has(item.id);
              return (
                <Fragment key={item.id}>
                  <TableRow
                    className="cursor-pointer hover:bg-surface-secondary"
                    onClick={() => toggleExpanded(item.id)}
                  >
                    <TableCell className="text-ink-muted">
                      <button
                        type="button"
                        aria-label={isOpen ? "Collapse full post" : "View full post"}
                        title={isOpen ? "Collapse full post" : "View full post"}
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleExpanded(item.id);
                        }}
                        className="rounded p-0.5 hover:bg-surface-secondary"
                      >
                        {isOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                      </button>
                    </TableCell>
                    <TableCell className="text-ink-muted">{item.id}</TableCell>
                    <TableCell className="whitespace-nowrap">{formatTime(item.time)}</TableCell>
                    <TableCell>{item.source_name}</TableCell>
                    <TableCell className="max-w-xs truncate" title={item.title}>
                      {item.title}
                    </TableCell>
                    <TableCell className="text-ink-muted">{item.story_id ?? "-"}</TableCell>
                    <TableCell>
                      <Badge tone={statusTone(item.status)}>{item.status}</Badge>
                    </TableCell>
                  </TableRow>
                  {isOpen && (
                    <TableRow className="bg-surface-secondary">
                      <TableCell colSpan={7} className="whitespace-normal py-4">
                        <div className="flex flex-col gap-2">
                          <p className="font-semibold text-ink-primary">{item.title}</p>
                          <p className="whitespace-pre-wrap text-sm text-ink-primary">
                            {item.body || "(no body text)"}
                          </p>
                          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted">
                            <span>Source: {item.source_name}</span>
                            <span>Time: {formatTime(item.time)}</span>
                            <span>Status: <Badge tone={statusTone(item.status)}>{item.status}</Badge></span>
                            <span>Story: {item.story_id ?? "none"}</span>
                          </div>
                          {item.status_reason && (
                            <p className="text-xs text-ink-muted">
                              → <StatusReason reason={item.status_reason} />
                            </p>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
            {data?.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="py-8 text-center text-ink-muted">
                  No posts for this filter.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <div className="flex items-center justify-between text-sm text-ink-muted">
        <span>
          Page {page + 1} of {totalPages} ({data?.total ?? 0} total)
        </span>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page === 0}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={page + 1 >= totalPages}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}
