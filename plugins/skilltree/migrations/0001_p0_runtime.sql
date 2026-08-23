CREATE TABLE schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE runtime_config (
  config_id INTEGER PRIMARY KEY CHECK(config_id = 1),
  config_version INTEGER NOT NULL CHECK(config_version >= 1),
  skill_root TEXT,
  skill_root_hash TEXT,
  trace_capture_enabled INTEGER NOT NULL CHECK(trace_capture_enabled IN (0,1)),
  memory_read_enabled INTEGER NOT NULL CHECK(memory_read_enabled IN (0,1)),
  memory_write_enabled INTEGER NOT NULL CHECK(memory_write_enabled IN (0,1)),
  replay_capture_enabled INTEGER NOT NULL CHECK(replay_capture_enabled IN (0,1)),
  updated_at TEXT NOT NULL,
  CHECK((skill_root IS NULL AND skill_root_hash IS NULL) OR
        (skill_root IS NOT NULL AND skill_root_hash IS NOT NULL AND length(skill_root_hash) = 71))
);

INSERT INTO runtime_config(
  config_id, config_version, skill_root, skill_root_hash,
  trace_capture_enabled, memory_read_enabled, memory_write_enabled, replay_capture_enabled, updated_at
) VALUES (1, 1, NULL, NULL, 0, 0, 0, 0, strftime('%Y-%m-%dT%H:%M:%fZ','now'));

CREATE TABLE audit_events (
  audit_id TEXT PRIMARY KEY,
  scope TEXT NOT NULL CHECK(scope IN ('user_global','workspace','plugin_global')),
  workspace_id TEXT,
  event_type TEXT NOT NULL,
  object_handle_hash TEXT,
  reason_code TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  retention_until TEXT NOT NULL
);

CREATE INDEX idx_audit_events_retention ON audit_events(retention_until);
