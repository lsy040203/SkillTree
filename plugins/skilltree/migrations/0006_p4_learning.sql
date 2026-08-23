CREATE TABLE skill_weights (
  workspace_id TEXT NOT NULL,
  skill_name TEXT NOT NULL,
  weight INTEGER NOT NULL DEFAULT 0 CHECK(weight >= -10 AND weight <= 10),
  last_signal_at TEXT,
  last_decay_at TEXT,
  last_updated_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  rule_version TEXT NOT NULL,
  PRIMARY KEY(workspace_id, skill_name)
);

CREATE INDEX idx_skill_weights_workspace ON skill_weights(workspace_id, weight DESC, skill_name);

CREATE TABLE skill_weight_updates (
  update_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  skill_name TEXT NOT NULL,
  source_type TEXT NOT NULL CHECK(source_type IN ('explicit_select','explicit_reject','explicit_switch','outcome','decay')),
  delta INTEGER NOT NULL CHECK(delta >= -2 AND delta <= 2),
  old_weight INTEGER NOT NULL CHECK(old_weight >= -10 AND old_weight <= 10),
  new_weight INTEGER NOT NULL CHECK(new_weight >= -10 AND new_weight <= 10),
  evidence_handle TEXT NOT NULL,
  evidence_quality TEXT NOT NULL CHECK(evidence_quality IN ('strict','relaxed','direct')),
  rule_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(workspace_id, skill_name, evidence_handle, rule_version)
);

CREATE INDEX idx_skill_weight_updates_workspace ON skill_weight_updates(workspace_id, created_at);
