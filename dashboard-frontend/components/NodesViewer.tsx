// Nodes tab: every LLM node in the pipeline — what it does, which model runs
// it, and its full system prompt straight from the source files on the VPS
// (see dashboard-backend/api/nodes.py). One accordion card per node so a
// list of up to 7 full prompts stays navigable instead of one giant page.
"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { useNodes } from "@/hooks/useApi";
import type { NodeInfo } from "@/lib/types";

function ModelChip({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-secondary px-2.5 py-1 text-xs font-medium text-ink-primary">
      <span className="text-ink-muted">{label}</span>
      {value}
    </span>
  );
}

function NodeCard({ node }: { node: NodeInfo }) {
  // Embeddings has no prompt to expand — nothing to collapse either.
  const [open, setOpen] = useState(false);
  const hasPrompt = Boolean(node.prompt);

  return (
    <Card>
      <CardHeader className="p-0">
        <button
          type="button"
          onClick={() => hasPrompt && setOpen((v) => !v)}
          aria-expanded={open}
          disabled={!hasPrompt}
          className="flex min-h-[44px] w-full items-start justify-between gap-3 p-4 text-left disabled:cursor-default"
        >
          <div className="flex flex-col gap-1.5">
            <span className="text-sm font-semibold text-ink-primary">{node.label}</span>
            <span className="text-xs text-ink-muted sm:text-sm">{node.description}</span>
            <div className="mt-1 flex flex-wrap gap-1.5">
              <ModelChip label="Model:" value={node.model} />
              {node.fallback_model && (
                <ModelChip label="Fallback:" value={node.fallback_model} />
              )}
            </div>
          </div>
          {hasPrompt ? (
            open ? (
              <ChevronDown className="mt-1 h-5 w-5 shrink-0 text-ink-muted" />
            ) : (
              <ChevronRight className="mt-1 h-5 w-5 shrink-0 text-ink-muted" />
            )
          ) : null}
        </button>
      </CardHeader>
      {hasPrompt && open && (
        <CardContent className="pt-0">
          <pre className="max-h-[28rem] overflow-y-auto whitespace-pre-wrap break-words rounded-md border border-border bg-surface-secondary p-3 font-mono text-xs leading-relaxed text-ink-primary">
            {node.prompt}
          </pre>
        </CardContent>
      )}
      {!hasPrompt && (
        <CardContent className="pt-0">
          <p className="text-xs text-ink-muted">
            No prompt — this step is a plain embedding call, not an LLM chat.
          </p>
        </CardContent>
      )}
    </Card>
  );
}

export function NodesViewer() {
  const { data, isFetching, isLoading } = useNodes();
  const nodes = data?.nodes ?? [];

  return (
    <div className="flex flex-col gap-4 p-4">
      {isFetching && <span className="text-xs text-ink-muted">Refreshing...</span>}

      {isLoading ? (
        <p className="text-sm text-ink-muted">Loading...</p>
      ) : (
        <div className="grid grid-cols-1 gap-3">
          {nodes.map((node) => (
            <NodeCard key={node.id} node={node} />
          ))}
          {nodes.length === 0 && (
            <p className="text-sm text-ink-muted">No node data available.</p>
          )}
        </div>
      )}
    </div>
  );
}
