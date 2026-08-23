CREATE TABLE turn_traces (
  turn_trace_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
  session_id_hash TEXT NOT NULL, workspace_id TEXT NOT NULL, turn_token_hash TEXT NOT NULL UNIQUE,
  soft_expires_at TEXT NOT NULL, hard_expires_at TEXT NOT NULL, consumed_at TEXT,
  prompt_hash TEXT NOT NULL, coverage_state TEXT NOT NULL, closed_at TEXT, retention_until TEXT NOT NULL,
  UNIQUE(session_id, turn_id), UNIQUE(session_id_hash, turn_id)
);
CREATE INDEX idx_turn_traces_retention ON turn_traces(retention_until);
CREATE TABLE run_turn_bindings (
  run_id TEXT PRIMARY KEY REFERENCES run_contexts(run_id) ON DELETE CASCADE,
  turn_trace_id TEXT NOT NULL UNIQUE REFERENCES turn_traces(turn_trace_id) ON DELETE CASCADE,
  bound_at TEXT NOT NULL, bind_state TEXT NOT NULL CHECK(bind_state IN ('normal','late')),
  UNIQUE(run_id, turn_trace_id)
);
