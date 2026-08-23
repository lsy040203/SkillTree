CREATE TABLE memory_write_breakers (
  workspace_id TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK(state IN ('closed','open','half_open')),
  consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_failures BETWEEN 0 AND 3),
  open_until TEXT,
  updated_at TEXT NOT NULL,
  retention_until TEXT NOT NULL,
  CHECK((state = 'open' AND open_until IS NOT NULL) OR (state IN ('closed','half_open') AND open_until IS NULL))
);

CREATE TABLE memory_candidates (
  candidate_id TEXT PRIMARY KEY,
  run_id TEXT REFERENCES run_contexts(run_id) ON DELETE SET NULL,
  workspace_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  layer TEXT NOT NULL CHECK(layer IN ('profile','procedure')),
  kind TEXT NOT NULL CHECK(kind IN ('identity','preference','procedure')),
  scope TEXT NOT NULL CHECK(scope IN ('user_global','workspace')),
  payload_json TEXT NOT NULL CHECK(length(payload_json) BETWEEN 2 AND 4096 AND json_valid(payload_json) AND json_type(payload_json) = 'object'),
  payload_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status = 'pending'),
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  retention_until TEXT NOT NULL,
  CHECK((layer = 'profile' AND kind IN ('identity','preference') AND scope = 'user_global')
     OR (layer = 'procedure' AND kind = 'procedure' AND scope = 'workspace'))
);

CREATE INDEX idx_memory_candidates_pending
  ON memory_candidates(workspace_id, user_id, status, expires_at);

CREATE TABLE profile_fields (
  profile_field_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  scope TEXT NOT NULL CHECK(scope = 'user_global'),
  namespace TEXT NOT NULL CHECK(namespace IN ('identity','preference')),
  field_key TEXT NOT NULL CHECK(field_key GLOB '[a-z][a-z0-9_]*' AND length(field_key) <= 64),
  value TEXT NOT NULL CHECK(length(value) BETWEEN 1 AND 256),
  value_hash TEXT NOT NULL,
  source_candidate_id TEXT REFERENCES memory_candidates(candidate_id) ON DELETE SET NULL,
  source_run_id TEXT REFERENCES run_contexts(run_id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  retention_until TEXT NULL CHECK(retention_until IS NULL),
  UNIQUE(user_id, namespace, field_key)
);

CREATE INDEX idx_profile_fields_user
  ON profile_fields(user_id, namespace, updated_at);

CREATE TABLE procedures (
  procedure_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  scope TEXT NOT NULL CHECK(scope = 'workspace'),
  rule TEXT NOT NULL CHECK(length(rule) BETWEEN 1 AND 500),
  rule_hash TEXT NOT NULL,
  shingle_fingerprint TEXT NOT NULL CHECK(length(shingle_fingerprint) = 71),
  applies_to TEXT NOT NULL CHECK(length(applies_to) BETWEEN 1 AND 80),
  scenario_key TEXT NOT NULL DEFAULT '' CHECK(length(scenario_key) <= 64),
  scenario_label TEXT NOT NULL DEFAULT '' CHECK(length(scenario_label) <= 120),
  recommended_skill_names_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(recommended_skill_names_json) AND json_type(recommended_skill_names_json) = 'array'),
  ordering_constraints_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(ordering_constraints_json) AND json_type(ordering_constraints_json) = 'array'),
  avoid_when TEXT NOT NULL DEFAULT '' CHECK(length(avoid_when) <= 300),
  strength TEXT NOT NULL CHECK(strength IN ('weak','strong')),
  importance_prior REAL NOT NULL DEFAULT 0.5 CHECK(importance_prior >= 0.0 AND importance_prior <= 1.0),
  reinforcement_count INTEGER NOT NULL DEFAULT 0 CHECK(reinforcement_count >= 0),
  seen_count INTEGER NOT NULL CHECK(seen_count >= 1),
  usage_score REAL NOT NULL DEFAULT 0.0 CHECK(usage_score >= 0.0 AND usage_score <= 1.0),
  recency_score REAL NOT NULL DEFAULT 1.0 CHECK(recency_score >= 0.0 AND recency_score <= 1.0),
  score REAL NOT NULL DEFAULT 0.0 CHECK(score >= 0.0 AND score <= 100.0),
  low_score_sweeps INTEGER NOT NULL DEFAULT 0 CHECK(low_score_sweeps >= 0),
  last_reinforced_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('active','hidden')),
  source_candidate_id TEXT REFERENCES memory_candidates(candidate_id) ON DELETE SET NULL,
  source_run_id TEXT REFERENCES run_contexts(run_id) ON DELETE SET NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  hidden_at TEXT,
  retention_until TEXT NOT NULL,
  CHECK((status = 'active' AND hidden_at IS NULL) OR (status = 'hidden' AND hidden_at IS NOT NULL)),
  UNIQUE(workspace_id, user_id, applies_to, scenario_key, rule_hash)
);

CREATE INDEX idx_procedures_recall
  ON procedures(workspace_id, user_id, applies_to, scenario_key, status, expires_at);

CREATE INDEX idx_procedures_fingerprint
  ON procedures(workspace_id, user_id, applies_to, scenario_key, shingle_fingerprint, status);

CREATE INDEX idx_procedures_sweep
  ON procedures(status, expires_at, low_score_sweeps, last_reinforced_at);
