// Main layout: header, tab nav, the active tab's content, and the global
// "API offline" banner driven by a lightweight health-check poll.
"use client";

import { useState } from "react";

import { GraphViewer } from "@/components/GraphViewer";
import { Header } from "@/components/Header";
import { NodesViewer } from "@/components/NodesViewer";
import { PostsFeed } from "@/components/PostsFeed";
import { StatsPanel } from "@/components/StatsPanel";
import { StoriesView } from "@/components/StoriesView";
import { TabNav, TABS, type Tab } from "@/components/TabNav";
import { Button } from "@/components/ui/button";
import { useHealth } from "@/hooks/useApi";
import { cn } from "@/lib/utils";

const TAB_CONTENT: Record<Tab, React.ComponentType> = {
  Posts: PostsFeed,
  Stories: StoriesView,
  Stats: StatsPanel,
  Graph: GraphViewer,
  Nodes: NodesViewer,
};

export default function Home() {
  const [tab, setTab] = useState<Tab>(TABS[0]);
  const health = useHealth();
  const offline = health.isError;

  const ActiveTab = TAB_CONTENT[tab];

  return (
    <div className="min-h-screen bg-surface-secondary">
      <Header />

      {offline && (
        <div className="flex items-center justify-between gap-3 bg-status-rejected/10 px-4 py-2 text-sm text-status-rejected">
          <span>API offline — showing last known data.{health.isFetching ? " Retrying..." : ""}</span>
          <Button variant="outline" size="sm" onClick={() => health.refetch()}>
            Retry
          </Button>
        </div>
      )}

      <TabNav active={tab} onChange={setTab} />

      <main className={cn("transition-opacity", offline && "pointer-events-none opacity-50")}>
        <ActiveTab />
      </main>
    </div>
  );
}
