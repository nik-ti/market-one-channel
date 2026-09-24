// Mirrors the Pydantic models in dashboard-backend/models/schemas.py.
// Kept in one file so a backend field rename is a one-place fix here.

export type ItemStatus = string;

export interface PostItem {
  id: number;
  time: string | null;
  source_name: string;
  title: string;
  body: string;
  story_id: number | null;
  status: ItemStatus;
  status_reason: string;
}

export interface PostsResponse {
  items: PostItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface StoryPost {
  id: number;
  item_id: number;
  title: string;
  body: string;
  status: "published" | "merged" | "rejected" | "held";
  status_reason: string;
}

export interface Story {
  id: number;
  headline: string;
  summary: string;
  status: string;
  item_count: number;
  post_count: number;
  state: string;
  last_post_at: string | null;
  posts: StoryPost[];
}

export interface StoriesResponse {
  stories: Story[];
}

export interface SourceCount {
  source_name: string;
  count: number;
}

export interface GateOutcomes {
  published: number;
  rejected: number;
  held: number;
  expired: number;
}

export interface TrendPoint {
  hour: string;
  count: number;
}

export interface StatsResponse {
  sources_count: SourceCount[];
  gate_outcomes: GateOutcomes;
  trends: TrendPoint[];
}

export interface GraphNode {
  id: string;
  label: string;
  last_invocation: string | null;
  error_count: number;
  health: "ok" | "degraded" | "error";
}

export interface GraphEdge {
  source: string;
  target: string;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface NodeInfo {
  id: string;
  label: string;
  description: string;
  model: string;
  fallback_model: string | null;
  prompt: string | null;
}

export interface NodesResponse {
  nodes: NodeInfo[];
}
