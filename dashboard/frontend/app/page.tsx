// Main layout: header (incl. channel switcher), tab nav, the active tab's
// content, and the global "API offline" banner driven by a lightweight
// health-check poll.
//
// The chosen channel lives in the URL's ?channel= query param, not
// localStorage, so a link to this dashboard carries the channel with it.
// Reading it needs next/navigation's useSearchParams, which Next.js requires
// to sit under a Suspense boundary even in an all-client page — hence the
// HomeContent/Home split below.
"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { GraphViewer } from "@/components/GraphViewer";
import { Header } from "@/components/Header";
import { NodesViewer } from "@/components/NodesViewer";
import { PostsFeed } from "@/components/PostsFeed";
import { StatsPanel } from "@/components/StatsPanel";
import { StoriesView } from "@/components/StoriesView";
import { TabNav, TABS, type Tab } from "@/components/TabNav";
import { Button } from "@/components/ui/button";
import { useChannels, useHealth } from "@/hooks/useApi";
import { DEFAULT_CHANNEL } from "@/lib/channel";
import { cn } from "@/lib/utils";

const TAB_CONTENT: Record<Tab, React.ComponentType<{ channel: string }>> = {
  Posts: PostsFeed,
  Stories: StoriesView,
  Stats: StatsPanel,
  Graph: GraphViewer,
  Nodes: NodesViewer,
};

function HomeContent() {
  const [tab, setTab] = useState<Tab>(TABS[0]);
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const channel = searchParams.get("channel") || DEFAULT_CHANNEL;

  const health = useHealth();
  const { data: channelsData } = useChannels();
  const offline = health.isError;

  const setChannel = useCallback(
    (next: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (next === DEFAULT_CHANNEL) params.delete("channel");
      else params.set("channel", next);
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams]
  );

  // A stale or mistyped ?channel= (an old shared link, a typo) snaps back to
  // the default once the real channel list is known, instead of leaving the
  // switcher showing a value it doesn't recognize.
  useEffect(() => {
    if (!channelsData) return;
    if (channelsData.channels.length === 0) return;
    const known = channelsData.channels.some((c) => c.id === channel);
    if (!known) setChannel(DEFAULT_CHANNEL);
  }, [channelsData, channel, setChannel]);

  const ActiveTab = TAB_CONTENT[tab];

  return (
    <div className="min-h-screen bg-surface-secondary">
      <Header channel={channel} channels={channelsData?.channels ?? []} onChannelChange={setChannel} />

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
        <ActiveTab channel={channel} />
      </main>
    </div>
  );
}

export default function Home() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-surface-secondary" />}>
      <HomeContent />
    </Suspense>
  );
}
