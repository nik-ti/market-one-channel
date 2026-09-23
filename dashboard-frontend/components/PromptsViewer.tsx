// Prompts tab: read-only view of each node's system prompt, straight from
// the source files on the VPS (see dashboard-backend/api/prompts.py).
"use client";

import { useState } from "react";

import { cn } from "@/lib/utils";
import { usePrompts } from "@/hooks/useApi";

const PROMPT_TABS = ["sorter", "gate", "writer", "editor"] as const;

export function PromptsViewer() {
  const { data, isFetching, isLoading } = usePrompts();
  const [active, setActive] = useState<(typeof PROMPT_TABS)[number]>("sorter");

  const text = data?.prompts[active] ?? "";

  return (
    <div className="flex flex-col gap-4 p-4">
      {isFetching && <span className="text-xs text-ink-muted">Refreshing...</span>}

      <div className="flex gap-1 overflow-x-auto">
        {PROMPT_TABS.map((name) => (
          <button
            key={name}
            onClick={() => setActive(name)}
            className={cn(
              "shrink-0 rounded-md px-3 py-1.5 text-sm font-medium capitalize",
              active === name
                ? "bg-ink-primary text-surface-primary"
                : "bg-surface-secondary text-ink-muted hover:text-ink-primary"
            )}
          >
            {name}
          </button>
        ))}
      </div>

      <div className="rounded-lg border border-border bg-surface-primary p-4">
        {isLoading ? (
          <p className="text-sm text-ink-muted">Loading...</p>
        ) : text ? (
          <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-ink-primary">
            {text}
          </pre>
        ) : (
          <p className="text-sm text-ink-muted">No prompt found for &quot;{active}&quot;.</p>
        )}
      </div>
    </div>
  );
}
