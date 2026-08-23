CREATE TABLE replay_capsules (
  replay_capsule_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE REFERENCES run_contexts(run_id) ON DELETE RESTRICT,
  workspace_id TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode = 'fixture_only'),
  consent_id TEXT,
  blob_handle TEXT,
  content_hash TEXT,
  status TEXT NOT NULL CHECK(status IN ('ready','rejected','expired','deleted')),
  expires_at TEXT,
  retention_until TEXT NOT NULL,
  created_at TEXT NOT NULL,
  CHECK(
    (status = 'ready' AND consent_id IS NOT NULL AND blob_handle IS NOT NULL AND content_hash IS NOT NULL AND expires_at IS NOT NULL)
    OR
    (status IN ('rejected','expired','deleted') AND consent_id IS NULL AND blob_handle IS NULL AND content_hash IS NULL AND expires_at IS NULL)
  )
);

CREATE TABLE evolution_candidates (
  candidate_id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('draft','replay_passed','rejected','rolled_back')),
  created_at TEXT NOT NULL,
  retention_until TEXT NOT NULL
);

CREATE INDEX idx_evolution_candidates_retention
  ON evolution_candidates(workspace_id, retention_until);

CREATE TABLE evolution_candidate_episode_refs (
  candidate_id TEXT NOT NULL REFERENCES evolution_candidates(candidate_id) ON DELETE CASCADE,
  episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE RESTRICT,
  PRIMARY KEY(candidate_id, episode_id)
);

CREATE TABLE replay_reports (
  report_id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL UNIQUE REFERENCES evolution_candidates(candidate_id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  retention_until TEXT NOT NULL
);

CREATE INDEX idx_replay_reports_retention
  ON replay_reports(retention_until);
