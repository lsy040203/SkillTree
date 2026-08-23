CREATE TABLE trace_events (
  event_id TEXT PRIMARY KEY,
  turn_trace_id TEXT NOT NULL REFERENCES turn_traces(turn_trace_id) ON DELETE CASCADE,
  run_id TEXT REFERENCES run_contexts(run_id) ON DELETE SET NULL,
  ingest_sequence INTEGER NOT NULL CHECK(ingest_sequence > 0),
  event_type TEXT NOT NULL,
  source TEXT NOT NULL,
  coverage_state TEXT NOT NULL CHECK(coverage_state IN ('observed','partial','unobserved','unattributed')),
  observed_at TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  payload_summary TEXT NOT NULL,
  tool_use_id TEXT,
  tool_name TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(turn_trace_id, ingest_sequence),
  UNIQUE(turn_trace_id, event_id)
);
CREATE INDEX idx_trace_events_turn_order ON trace_events(turn_trace_id, ingest_sequence);

CREATE TABLE hook_observations (
  hook_bundle_hash TEXT PRIMARY KEY,
  first_observed_at TEXT NOT NULL,
  last_observed_at TEXT NOT NULL,
  observed_count INTEGER NOT NULL CHECK(observed_count > 0),
  last_event_id TEXT NOT NULL
);

CREATE TABLE outcome_assessments (
  assessment_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES run_contexts(run_id) ON DELETE CASCADE,
  turn_trace_id TEXT NOT NULL REFERENCES turn_traces(turn_trace_id) ON DELETE CASCADE,
  event_id TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL CHECK(source IN ('user','read_only_verifier','tool_adapter')),
  verdict TEXT NOT NULL CHECK(verdict IN ('success','failed','cancelled','unknown')),
  outcome_summary TEXT NOT NULL,
  evidence_ref TEXT,
  observed_at TEXT NOT NULL,
  supersedes_event_id TEXT,
  UNIQUE(run_id, assessment_id)
);

CREATE TABLE episodes (
  episode_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE REFERENCES run_contexts(run_id) ON DELETE CASCADE,
  turn_trace_id TEXT NOT NULL UNIQUE REFERENCES turn_traces(turn_trace_id) ON DELETE CASCADE,
  objective_hash TEXT NOT NULL,
  objective_preview TEXT NOT NULL,
  trusted_skill_snapshot TEXT NOT NULL,
  snapshot_partial INTEGER NOT NULL CHECK(snapshot_partial IN (0,1)),
  trace_state TEXT NOT NULL CHECK(trace_state IN ('complete','incomplete','flush_failed')),
  coverage_state TEXT NOT NULL CHECK(coverage_state IN ('observed','partial','unobserved','unattributed')),
  verdict TEXT NOT NULL CHECK(verdict IN ('success','failed','cancelled','unknown')),
  event_count INTEGER NOT NULL CHECK(event_count >= 0),
  outcome_ref TEXT,
  created_at TEXT NOT NULL,
  retention_until TEXT NOT NULL
);
