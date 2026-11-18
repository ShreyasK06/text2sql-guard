export type Role = "analyst" | "manager" | "admin";
export type Region = "north" | "south" | "east" | "west";

export interface QueryRequest {
  question?: string;
  sql?: string;
  role: Role;
  region?: Region;
}

export interface GuardResult {
  layer: string;
  decision: "allow" | "block";
  rule_id: string | null;
  reason: string;
  latency_ms: number;
  details?: Record<string, unknown>;
}

export interface SelfCheck {
  verdict: "yes" | "no" | "unverified" | string;
  explanation: string;
}

export interface QueryResponse {
  request_id: string;
  decision: "allow" | "block" | "error" | string;
  blocked_by: string | null;
  rule_id: string | null;
  reason: string;
  sql_generated: string | null;
  sql_executed: string | null;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  masked_columns: string[];
  redactions: number;
  retries: number;
  self_check: SelfCheck | null;
  latency_ms: number;
  layer_latency_ms: Record<string, number>;
  guard_results: GuardResult[];
  config: string;
}

export interface HealthResponse { status: string; db: boolean; provider: string }
export interface LlmStatus {
  provider: string;
  model: string | null;
  configured: boolean;
  mode: "mock" | "live";
  privacy_note: string;
  detail?: string;
}

export interface PolicyResponse {
  role: string;
  description: string;
  row_scope: string;
  plain_columns: Record<string, string[]>;
  masked_columns: Record<string, Record<string, string>>;
  visible_tables: string[];
}

export interface CatalogResponse {
  roles: { name: string; description: string; row_scope: string }[];
  schema: Record<string, string[]>;
  sensitive_columns: Record<string, string>;
  limits: Record<string, number>;
}

export interface AuditEntry {
  ts: string;
  request_id: string;
  role: string;
  region: string | null;
  question: string | null;
  sql_generated: string | null;
  sql_executed: string | null;
  decision: string;
  blocked_by: string | null;
  rule_id: string | null;
  reason: string;
  guard_results: GuardResult[];
}

export interface DemoCase { id: string; title: string; request: QueryRequest; response: QueryResponse }
export interface DemoData {
  recorded_at: string;
  provider: string;
  catalog: CatalogResponse;
  policies: Record<string, PolicyResponse>;
  cases: DemoCase[];
  audit: AuditEntry[];
}

export interface LatencyRow { scope: string; n: number; median_ms: number | null; p95_ms: number | null }
export interface ResultsSplit {
  n_benign: number;
  n_attacks: number;
  false_block: { rate: number; ci_lo: number; ci_hi: number; n: number };
  false_blocks: { id: string; role: string; layer: string; rule_id: string; reason: string }[];
  latency_per_layer: LatencyRow[];
  end_to_end: {
    full_median_ms: number; full_p95_ms: number; none_median_ms: number; none_p95_ms: number;
    median_overhead_ms: number; n: number;
  };
  ablation_benign: { config: string; n: number; false_blocks: number; false_block_rate: number | null }[];
}
export interface Results {
  tests: { passed: number; failed: number; skipped: number } | null;
  splits: { dev: ResultsSplit; heldout: ResultsSplit };
}
