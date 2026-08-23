CREATE TABLE skills (
  name TEXT PRIMARY KEY,
  description TEXT NOT NULL DEFAULT '' CHECK(length(description) <= 500),
  path TEXT NOT NULL UNIQUE,
  content_hash TEXT NOT NULL CHECK(length(content_hash) = 71),
  state TEXT NOT NULL CHECK(state IN ('pending','trusted','blocked','invalid','out_of_scope')),
  diagnostic TEXT,
  updated_at TEXT NOT NULL,
  CHECK((state != 'invalid' AND length(description) BETWEEN 1 AND 500) OR state = 'invalid')
);
