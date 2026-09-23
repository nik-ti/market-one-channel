// Graph tab: the pipeline as a Mermaid diagram (node color = health), plus
// the full workflow write-up underneath. Mermaid only runs in the browser
// (it touches `document`), so it's dynamically imported inside an effect
// rather than imported at module scope.
"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { useGraph } from "@/hooks/useApi";
import type { GraphNode } from "@/lib/types";
import { WORKFLOW_EXPLANATION } from "@/lib/workflowExplanation";

const HEALTH_CLASS: Record<GraphNode["health"], string> = {
  ok: "healthy",
  degraded: "warning",
  error: "unhealthy",
};

function formatTime(iso: string | null) {
  if (!iso) return "never";
  const date = new Date((iso.includes("T") ? iso : iso.replace(" ", "T")) + "Z");
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

// Builds Mermaid flowchart source from the live node list: 7 boxes in a
// row, an arrow to the next stage, colored by health, each clickable via
// the window callback wired up in the effect below.
function buildDiagram(nodes: GraphNode[]): string {
  if (nodes.length === 0) return "flowchart LR\n  empty[No data yet]";

  const lines = ["flowchart LR"];
  nodes.forEach((node, i) => {
    const label = node.label.replace(/"/g, "'");
    lines.push(`  ${node.id}["${label}"]:::${HEALTH_CLASS[node.health]}`);
    if (i < nodes.length - 1) {
      lines.push(`  ${node.id} --> ${nodes[i + 1].id}`);
    }
  });
  nodes.forEach((node) => {
    lines.push(`  click ${node.id} call marketOneGraphNodeClick("${node.id}")`);
  });
  lines.push("  classDef healthy fill:#10B981,stroke:#059669,color:#ffffff,stroke-width:2px");
  lines.push("  classDef warning fill:#F59E0B,stroke:#D97706,color:#ffffff,stroke-width:2px");
  lines.push("  classDef unhealthy fill:#DC2626,stroke:#B91C1C,color:#ffffff,stroke-width:2px");
  return lines.join("\n");
}

export function GraphViewer() {
  const { data, isFetching } = useGraph();
  const [selected, setSelected] = useState<string | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const nodes = data?.nodes ?? [];
  const selectedNode = nodes.find((n) => n.id === selected) ?? null;

  useEffect(() => {
    (window as unknown as Record<string, unknown>).marketOneGraphNodeClick = (id: string) => {
      setSelected((prev) => (prev === id ? null : id));
    };
    return () => {
      delete (window as unknown as Record<string, unknown>).marketOneGraphNodeClick;
    };
  }, []);

  useEffect(() => {
    if (nodes.length === 0 || !containerRef.current) return;
    let cancelled = false;

    import("mermaid").then(async ({ default: mermaid }) => {
      mermaid.initialize({ startOnLoad: false, securityLevel: "loose", theme: "base" });
      try {
        const { svg, bindFunctions } = await mermaid.render(
          `pipeline-graph-${Date.now()}`,
          buildDiagram(nodes)
        );
        if (cancelled || !containerRef.current) return;
        containerRef.current.innerHTML = svg;
        bindFunctions?.(containerRef.current);
        setRenderError(null);
      } catch (err) {
        if (!cancelled) {
          setRenderError(err instanceof Error ? err.message : String(err));
        }
      }
    });

    return () => {
      cancelled = true;
    };
    // Re-render whenever node health/labels change (e.g. after a poll).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(nodes)]);

  return (
    <div className="flex flex-col gap-4 p-4">
      {isFetching && <span className="text-xs text-ink-muted">Refreshing...</span>}

      <div className="overflow-x-auto rounded-lg border border-border bg-surface-primary p-4">
        {renderError ? (
          <p className="text-sm text-status-rejected">Diagram failed to render: {renderError}</p>
        ) : (
          <div
            ref={containerRef}
            className="flex min-w-[560px] justify-center [&_svg]:h-auto [&_svg]:max-w-none"
          />
        )}
      </div>

      {selectedNode ? (
        <div className="rounded-lg border border-border bg-surface-primary p-4 text-sm">
          <p className="font-semibold text-ink-primary">{selectedNode.label}</p>
          <p className="text-ink-muted">Health: {selectedNode.health}</p>
          <p className="text-ink-muted">Last invocation: {formatTime(selectedNode.last_invocation)}</p>
          <p className="text-ink-muted">Error count: {selectedNode.error_count}</p>
        </div>
      ) : (
        <p className="text-xs text-ink-muted">Click a node in the diagram to see its metrics.</p>
      )}

      <div className="rounded-lg border border-border bg-surface-primary p-4 md:p-6">
        <article className="prose-workflow">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              // Markdown tables don't wrap themselves — without this a wide
              // table (the "Dashboard Summary" one) overflows at 375px.
              table: ({ ...props }) => (
                <div className="table-scroll">
                  <table {...props} />
                </div>
              ),
            }}
          >
            {WORKFLOW_EXPLANATION}
          </ReactMarkdown>
        </article>
      </div>
    </div>
  );
}
