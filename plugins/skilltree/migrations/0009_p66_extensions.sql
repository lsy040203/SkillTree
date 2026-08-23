CREATE TABLE replay_extensions (
  extension_id TEXT PRIMARY KEY,
  extension_version TEXT NOT NULL,
  adapter_name TEXT NOT NULL,
  task_types_json TEXT NOT NULL CHECK(json_valid(task_types_json) AND json_type(task_types_json) = 'array'),
  manifest_hash TEXT NOT NULL CHECK(length(manifest_hash) = 71),
  image_name TEXT NOT NULL,
  image_digest TEXT NOT NULL CHECK(length(image_digest) = 71),
  trust_state TEXT NOT NULL CHECK(trust_state IN ('official','local_unverified','revoked','disabled')),
  install_state TEXT NOT NULL CHECK(install_state IN ('installed','failed','removed')),
  installed_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(extension_id, extension_version, manifest_hash)
);

CREATE INDEX idx_replay_extensions_install_state
  ON replay_extensions(install_state);

CREATE INDEX idx_replay_extensions_trust_state
  ON replay_extensions(trust_state);

CREATE INDEX idx_replay_extensions_updated_at
  ON replay_extensions(updated_at);
