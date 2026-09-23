// Posts tab (default tab): filterable, paginated feed of everything the
// pipeline has ingested. Polls every 10s via usePosts().
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

const PAGE_SIZE = 50;

function formatTime(iso: string | null) {
  if (!iso) return "-";
  // DB times are stored as naive UTC strings ("YYYY-MM-DD HH:MM:SS").
  const withZone = iso.includes("T") ? iso : `${iso.replace(" ", "T")}Z`;
  const date = new Date(withZone);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
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
          className="rounded-md border border-border bg-surface-primary px-3 py-1.5 text-sm text-ink-primary"
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

      <div className="table-scroll rounded-lg border border-border bg-surface-primary">
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
