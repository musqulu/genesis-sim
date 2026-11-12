export interface Cell {
  x: number;
  y: number;
}

export interface CellState extends Cell {
  energy: number;
  loose_material: number;
  packed_material: number;
}

export interface OrganismView {
  id: string;
  label: string;
  policy: string;
  generation: number;
  birth_tick: number;
  x: number;
  y: number;
  energy: number;
  energy_capacity: number;
  age: number;
  alive: boolean;
  death_tick: number | null;
  death_cause: string | null;
  retired_energy: number;
  carried_material: number;
  material_capacity: number;
  retired_material: number;
  lifespan: number | null; // withheld while alive
  parent_ids: string[]; // v9: empty for seed organisms
}

export interface Ledger {
  energy: {
    initial: number;
    in_cells: number;
    in_organisms: number;
    environmental_inflow: number;
    dissipation: number;
    death_retirement: number;
    expected: number;
    actual: number;
  };
  material: {
    initial: number;
    retired: number;
    expected: number;
    loose: number;
    packed: number;
    carried: number;
    actual: number;
  };
  balanced: boolean;
}

export interface ReplaySource {
  kind: "snapshot" | "initial";
  snapshot_tick: number | null;
  ticks_replayed: number;
  events_applied: number;
  skipped_snapshots: { tick: number; reason: string }[];
}

/** An accepted transfer or message committed in a frame's tick (observer view). */
export interface Interaction {
  kind: "transfer" | "communicate" | "reproduce";
  from_id: string;
  to_id: string;
  from_x: number;
  from_y: number;
  to_x: number;
  to_y: number;
  detail: string;
}

/** What a renderer or inspector needs; satisfied by live and historical frames. */
export interface WorldView {
  config: GridFrame["config"];
  tick: number;
  cells: CellState[];
  changed_cells: number[];
  changed_organisms: string[];
  interactions: Interaction[];
  organisms: OrganismView[];
  extinct: boolean;
  ledger: Ledger;
  state_hash: string;
}

export interface GridFrame {
  schema_version: 9;
  mode: "live";
  run_id: string;
  created_at: string;
  engine_version: string;
  engine_source_hash: string;
  rules_version: string;
  state_schema: number;
  manifest_hash: string;
  config: {
    width: number;
    height: number;
    seed: number;
    energy_cap: number;
    packed_cap: number;
    inflow_interval: number;
    inflow_amount: number;
    fixture: string | null;
  };
  tick: number;
  status: "paused" | "running";
  started: boolean;
  speed: number;
  until_tick: number | null;
  last_event_sequence: number;
  changed_cells: number[];
  changed_organisms: string[];
  interactions: Interaction[];
  organisms: OrganismView[];
  extinct: boolean;
  last_error: string | null;
  pending: PendingTick | null;
  provider: ProviderInfo | null;
  report_path: string | null;
  restored_from: ReplaySource | null;
  dev_fixtures: string[];
  journal_mode: string;
  cells: CellState[];
  ledger: Ledger;
  state_hash: string;
}

export interface PendingTick {
  tick: number;
  total: number;
  completed: number;
  failed: number;
  waiting: string[];
  seconds: number;
}

export interface ProviderInfo {
  kind: string;
  name: string;
  model: string | null;
  revision: string | null;
  capabilities: {
    structured_output: string;
    max_context_tokens: number | null;
    max_output_tokens: number | null;
    concurrency: number;
  };
  health: ProviderHealth | null;
  profile: Record<string, unknown> | null;
  /** Mixed-model runs: each organism's own model, fixed for life. */
  members?: Record<string, ProviderMember> | null;
}

export interface ProviderMember {
  kind: string;
  name: string;
  model: string | null;
  revision: string | null;
  health: ProviderHealth | null;
}

export interface PopulationEntry {
  cognition: string;
  organisms: number;
}

/** Short words for one organism's model ("local Qwen…" / "API deepseek…"). */
export function memberLabel(member: ProviderMember): string {
  if (member.kind === "mlx") return `local MLX-LM · ${member.name}`;
  if (member.kind === "api")
    return `${member.health?.remote ? "remote API" : "self-hosted API"} · ${member.model ?? member.name}`;
  return `${member.kind} · ${member.name}`;
}

export interface ProviderHealth {
  status: string;
  pid: number | null;
  load_seconds: number | null;
  memory_gb: number | null;
  requests: number;
  failures: number;
  restarts?: number;
  last_latency_ms: number | null;
  runtime?: Record<string, string> | null;
  // API providers (M07B)
  endpoint?: string;
  remote?: boolean;
  last_error_kind?: string | null;
  budget?: {
    requests: number;
    max_requests: number;
    tokens: number;
    max_total_tokens: number;
  };
}

export interface DecisionRecord {
  request_id: string;
  tick: number;
  actor_id: string;
  input_hash: string;
  provider: Record<string, unknown>;
  raw_output: string;
  parse_status: string;
  parse_error: string | null;
  proposal: Record<string, unknown> | null;
  outcome: "accepted" | "rejected" | "unparsed";
  outcome_rule: string;
  outcome_reason: string | null;
  event_sequence: number;
  usage: {
    input_tokens?: number | null;
    output_tokens?: number | null;
    latency_ms?: number | null;
    reused?: boolean;
    finish_reason?: string | null;
  };
  prompt?: { role: string; content: string }[] | null;
  detail?: Record<string, unknown> | null;
  request?: {
    request_id: string;
    target_tick: number;
    schema_version: string;
    input_hash: string;
    purpose?: "decide" | "reflect"; // M11: a reflection replaces a decision
    context: {
      // Decisions only; a reflection request carries `status` and `memories` instead.
      observation?: Observation;
      previous_result?: Record<string, unknown> | null;
      action_schema?: Record<string, unknown>;
      response_format: string;
      status?: { age: number; energy: number };
      memories?: RecalledMemory[];
      // State schema 7 (M08): memory rules/status and the recalled records given.
      memory?: MemoryStatus;
      recalled?: RecalledMemory[];
      // State schema 8 (M09): items delivered since its last decision.
      inbox?: InboxItem[];
    };
    recall?: RecallInfo | null;
  } | null;
}

/** A delivered inbox item as the recipient received it. */
export interface InboxItem {
  id: string;
  kind: "message" | "transfer";
  from: string;
  ticks_ago: number;
  text: string;
}

/** What the organism received of one of its own records (offsets, never coordinates). */
export interface RecalledMemory {
  id: string;
  kind: string;
  ticks_ago: number;
  age_then: number;
  dx: number;
  dy: number;
  text: string;
  cites?: string[]; // reflections: the records they cite
}

export interface MemoryStatus {
  rules: string;
  active_records: number;
  active_bytes: number;
  max_records: number;
  max_bytes: number;
}

/** Observer-only account of how recall chose the records (recall-v1). */
export interface RecallInfo {
  version: string;
  query: string[];
  candidates: number;
  selected_ids: string[];
  scores: {
    id: string;
    score: number;
    recency: number;
    relevance: number;
    salience: number;
    selected: boolean;
  }[];
}

export async function requestDecisions(
  organismId: string,
  maxTick?: number,
): Promise<{ run_id: string; decisions: DecisionRecord[] }> {
  const query = maxTick === undefined ? "" : `&max_tick=${maxTick}`;
  return request(
    `/api/decisions?organism_id=${encodeURIComponent(organismId)}&limit=50${query}`,
  );
}

export async function requestDecision(
  requestId: string,
): Promise<DecisionRecord> {
  return request(`/api/decisions/${encodeURIComponent(requestId)}`);
}

export interface HistoryFrame extends WorldView {
  schema_version: 9;
  mode: "history";
  run_id: string;
  live_tick: number;
  state_schema: number;
  recorded_hash: string;
  verified: boolean;
  source: ReplaySource;
}

export interface SnapshotInfo {
  tick: number;
  reason: string;
  created_at: string;
  checksum_ok: boolean;
}

export interface EventSummary {
  sequence: number;
  tick: number;
  phase: string;
  type: string;
  rule_id: string;
  cause: string;
  actor_id: string | null;
  summary: Record<string, number | string>;
}

export interface ObservedCell {
  dx: number;
  dy: number;
  energy: number;
  loose_material: number;
  packed_material: number;
  occupied: boolean;
}

export interface Observation {
  organism_id: string;
  age: number;
  energy: number;
  energy_capacity: number;
  carried_material: number;
  material_capacity: number;
  upkeep_per_tick: number;
  perception_radius: number;
  life_note: string;
  actions: string[];
  cells: ObservedCell[];
  organisms: { id: string; dx: number; dy: number }[];
}

export interface DecisionPayload {
  request_id?: string; // M06+: the decision request this outcome consumed
  observation: Observation;
  observation_sha256: string;
  origin: { x: number; y: number };
  proposal: { action: unknown; direction?: unknown };
  explanation?: string | null;
  policy: string;
}

export interface CellDelta {
  x: number;
  y: number;
  field: string;
  before: number;
  after: number;
  delta: number;
}

export interface EventDetail extends EventSummary {
  run_id: string;
  payload: Partial<DecisionPayload>;
  organism_changes: {
    organism_id: string;
    field: string;
    before: unknown;
    after: unknown;
  }[];
  changes: CellDelta[];
  ledger: { resource: string; category: string; amount: number }[];
}

export interface CellRecentChange {
  event_sequence: number;
  tick: number;
  type: string;
  rule_id: string;
  actor_id: string | null;
  field: string;
  before: number;
  after: number;
  delta: number;
}

export interface CellInspection {
  run_id: string;
  tick: number;
  index: number;
  cell: CellState;
  entry_cost: number | null;
  recent_changes: CellRecentChange[];
}

export type ControlCommand =
  | { command: "start" | "resume"; until_tick?: number }
  | { command: "pause" | "step" }
  | { command: "speed"; ticks_per_second: number };

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export const FRAME_VERSION = 9;

/** The backend answered, but with a frame contract this page does not understand. */
export class IncompatibleBackend extends Error {
  constructor(readonly received: unknown) {
    super(
      `The backend sent frame version ${String(received)}; this page expects version ${FRAME_VERSION}.`,
    );
  }
}

export function newCommandId(): string {
  const random = globalThis.crypto?.randomUUID?.();
  return (
    random ??
    `cmd-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
  );
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    signal: init.signal ?? AbortSignal.timeout(4000),
    headers: { "Content-Type": "application/json", ...init.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    let detail = `Backend returned ${response.status}`;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // Keep the generic message.
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

function validWorld(frame: WorldView & { schema_version: number }): boolean {
  const c = frame?.config;
  return !(
    frame?.schema_version !== FRAME_VERSION ||
    !Number.isInteger(frame.tick) ||
    frame.tick < 0 ||
    !Number.isInteger(c?.width) ||
    !Number.isInteger(c?.height) ||
    c.width < 1 ||
    c.height < 1 ||
    c.width > 128 ||
    c.height > 128 ||
    !Number.isInteger(c.energy_cap) ||
    !Array.isArray(frame.cells) ||
    frame.cells.length !== c.width * c.height ||
    !frame.cells.every(
      (cell, i) =>
        cell.x === i % c.width &&
        cell.y === Math.floor(i / c.width) &&
        Number.isInteger(cell.energy) &&
        Number.isInteger(cell.loose_material) &&
        Number.isInteger(cell.packed_material),
    ) ||
    !Array.isArray(frame.changed_cells) ||
    !Array.isArray(frame.organisms) ||
    typeof frame.extinct !== "boolean" ||
    !frame.ledger ||
    !/^[a-f0-9]{64}$/.test(frame.state_hash)
  );
}

export function validateFrame(frame: GridFrame): GridFrame {
  // Refuse incompatible or incomplete frames instead of inventing display data.
  if (
    frame &&
    typeof frame === "object" &&
    "schema_version" in frame &&
    frame.schema_version !== FRAME_VERSION
  )
    throw new IncompatibleBackend(frame.schema_version);
  if (
    !validWorld(frame) ||
    frame.mode !== "live" ||
    (frame.status !== "paused" && frame.status !== "running") ||
    typeof frame.run_id !== "string"
  ) {
    throw new Error("The backend returned an incompatible grid frame");
  }
  return frame;
}

export function validateHistory(frame: HistoryFrame): HistoryFrame {
  if (
    !validWorld(frame) ||
    frame.mode !== "history" ||
    typeof frame.verified !== "boolean" ||
    !frame.source
  ) {
    throw new Error("The backend returned an incompatible history frame");
  }
  return frame;
}

export async function requestWorld(): Promise<GridFrame> {
  return validateFrame(await request<GridFrame>("/api/world"));
}

export async function createRun(
  seed: number,
  fixture: string | null,
  cognition: string | null = null,
  organisms = 1,
  population: PopulationEntry[] | null = null,
  size = 32,
  tendencies: "seeded" | "neutral" = "seeded",
): Promise<GridFrame> {
  const body: Record<string, unknown> = { seed };
  if (!fixture && size !== 32) body.size = size; // square map side (fixtures keep 32)
  if (!fixture && tendencies !== "seeded") body.tendencies = tendencies; // paired control
  if (population) {
    body.population = population; // a mixed-model experiment
  } else {
    if (fixture) body.fixture = fixture;
    if (cognition) {
      body.cognition = cognition;
      body.organisms = organisms;
    }
  }
  return validateFrame(
    await request<GridFrame>("/api/runs", {
      method: "POST",
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30000), // a model profile reads local files
    }),
  );
}

export interface LocalModel {
  id: string;
  path: string;
  model_type: string | null;
  size_gb: number;
}

export interface ModelsInfo {
  runtime: {
    available: boolean;
    reason?: string;
    mlx?: string;
    mlx_lm?: string;
  };
  model_dirs: string[];
  models: LocalModel[];
  api?: {
    file: string;
    problems: string[];
    profiles: ApiProfile[];
  };
}

/** A configured API connection profile. Keys never reach the browser. */
export interface ApiProfile {
  id: string;
  protocol: string;
  label: string;
  host: string;
  model: string;
  remote: boolean;
  key_status: string;
  api_key_env: string | null;
  available: boolean;
  reason: string | null;
}

export interface ProviderCheck {
  ok: boolean | null;
  message: string;
}

/** A cheap connectivity probe of one API profile (creates nothing). */
export async function checkProvider(id: string): Promise<ProviderCheck> {
  return request(`/api/providers/${encodeURIComponent(id)}/check`, {
    method: "POST",
    signal: AbortSignal.timeout(30000),
  });
}

export interface RunSummary {
  run_id: string;
  path: string;
  markdown: string;
  status?: string;
}

/** Write (or refresh) the plain-English summary of the active run. */
export async function writeSummary(): Promise<RunSummary> {
  return request("/api/summary", {
    method: "POST",
    signal: AbortSignal.timeout(30000),
  });
}

export async function readSummary(): Promise<RunSummary> {
  return request("/api/summary");
}

export async function requestModels(): Promise<ModelsInfo> {
  return request("/api/models", { signal: AbortSignal.timeout(60000) });
}

export async function sendControl(
  command: ControlCommand,
  commandId: string = newCommandId(),
): Promise<GridFrame> {
  return validateFrame(
    await request<GridFrame>("/api/control", {
      method: "POST",
      body: JSON.stringify({ ...command, command_id: commandId }),
    }),
  );
}

export async function requestEvents(
  before?: number,
  maxTick?: number,
): Promise<{ run_id: string; events: EventSummary[]; has_more: boolean }> {
  const query =
    (before === undefined ? "" : `&before=${before}`) +
    (maxTick === undefined ? "" : `&max_tick=${maxTick}`);
  return request(`/api/events?limit=50${query}`);
}

export async function requestHistory(
  tick: number,
  source: "snapshot" | "initial",
): Promise<HistoryFrame> {
  return validateHistory(
    await request<HistoryFrame>(`/api/history/${tick}?source=${source}`, {
      signal: AbortSignal.timeout(15000),
    }),
  );
}

export async function requestSnapshots(): Promise<{
  run_id: string;
  snapshots: SnapshotInfo[];
}> {
  return request("/api/snapshots");
}

export async function requestEvent(sequence: number): Promise<EventDetail> {
  return request(`/api/events/${sequence}`);
}

export interface MemoryRecordView {
  id: string;
  owner_id: string;
  kind: string;
  tick: number;
  age: number;
  x: number;
  y: number;
  text: string;
  keywords: string[];
  salience: number;
  bytes: number;
  evidence?: string[]; // reflections (M11): ids of the records cited
  origin?: {
    from: string;
    source_id: string;
    source_kind: string;
    source_tick: number;
    ancestor_id: string;
  }; // inherited records (M13)
  added_sequence: number | null;
  forgotten_tick?: number;
  forgotten_sequence?: number;
}

export interface MemoryDetail {
  limits: {
    max_records: number;
    max_bytes: number;
    record_max_bytes: number;
    write_fee_per_kib: number;
    upkeep_per_kib: number;
    upkeep_interval: number;
    recall_max: number;
    recall_max_bytes: number;
  };
  active_records: number;
  active_bytes: number;
  written_total: number;
  active: MemoryRecordView[]; // newest first
  forgotten: MemoryRecordView[]; // observer only; newest first
  last_recall: {
    tick: number;
    recall: RecallInfo | null;
    recalled: RecalledMemory[] | null;
  } | null;
}

export interface OrganismDetail {
  run_id: string;
  tick: number;
  mode: "live" | "history";
  organism: OrganismView;
  latest_decision: EventDetail | null;
  events: EventSummary[];
  action_preview: ActionPreview[];
  memory?: MemoryDetail | null; // state schema 7 runs only
  inheritance?: BirthRecord | null; // organisms born in the run (M12/M13)
  messages?: MessagesDetail | null; // state schema 8 runs only
  profile?: {
    tendencies: Record<string, number> | null;
    tendency_mode: string;
    stats: BehaviourStats | null;
  } | null; // state schema 9 runs only
}

/** How a run-born organism came to be: the logged inheritance draws (M12/M13). */
export interface BirthRecord {
  event_sequence: number;
  tick: number;
  parent_id: string;
  generation: number;
  inheritance: {
    tendencies?: null;
    mask?: boolean[];
    parent?: number[];
    child?: number[];
    memories?: {
      selected: string[];
      retained: string[];
      dropped: string[];
      copies: string[];
    };
  } | null;
}

/** Counts derived from recorded events (descriptive; not measures of intent). */
export interface BehaviourStats {
  decisions: number;
  accepted: number;
  rejected: number;
  unparsed: number;
  actions: Record<string, number>;
  cells_visited: number;
  energy_extracted: number;
  material_extracted: number;
  energy_spent_on_actions: number;
  gifts_given: number;
  gifts_received: number;
  messages_sent: number;
  messages_received: number;
  material_packed: number;
  material_unpacked: number;
  children: number; // M12: births as a parent
}

export interface PopulationInfo {
  run_id: string;
  tick: number;
  tendency_names: string[];
  tendency_mode: string | null;
  organisms: {
    id: string;
    alive: boolean;
    death_tick: number | null;
    generation?: number;
    parent_ids?: string[];
    tendencies: Record<string, number> | null;
    stats: BehaviourStats | null;
  }[];
}

/** A key moment of the run for history navigation (M14). */
export interface KeyMoment {
  sequence: number;
  tick: number;
  kind: "birth" | "death" | "modification" | "extinction";
  actor_id: string | null;
  text: string;
}

export async function requestMoments(): Promise<{
  run_id: string;
  moments: KeyMoment[];
}> {
  return request("/api/moments");
}

export async function requestPopulation(
  maxTick?: number,
): Promise<PopulationInfo> {
  return request(
    `/api/population${maxTick === undefined ? "" : `?at_tick=${maxTick}`}`,
  );
}

/** One item an organism sent or received, with its latest recorded state. */
export interface MessageRecord {
  id: string;
  kind: "message" | "transfer";
  sender_id: string;
  recipient_id: string;
  text: string;
  sent_tick: number;
  status: "pending" | "delivered";
  delivered_tick: number | null;
  state: string; // pending | delivered | read | expired | recipient_dead
  ended_tick?: number;
}

export interface MessagesDetail {
  inbox: MessageRecord[];
  inbox_capacity: number;
  recent: MessageRecord[]; // newest first
}

export interface ActionPreview {
  proposal: Record<string, unknown>;
  accepted: boolean;
  reason: string | null;
  energy_cost: number;
}

export async function requestOrganism(
  id: string,
  atTick?: number,
): Promise<OrganismDetail> {
  const query = atTick === undefined ? "" : `?at_tick=${atTick}`;
  return request(`/api/organisms/${encodeURIComponent(id)}${query}`);
}

export async function requestCell(
  cell: Cell,
  atTick?: number,
): Promise<CellInspection> {
  const query = atTick === undefined ? "" : `?at_tick=${atTick}`;
  return request(`/api/cells/${cell.x}/${cell.y}${query}`);
}
