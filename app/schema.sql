PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  filename TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL,
  page_no INTEGER NOT NULL,
  text TEXT NOT NULL,
  thumb_path TEXT,
  FOREIGN KEY(document_id) REFERENCES documents(id),
  UNIQUE(document_id, page_no)
);

CREATE VIRTUAL TABLE IF NOT EXISTS page_fts USING fts5(
  text,
  document_id UNINDEXED,
  page_no UNINDEXED,
  content='pages',
  content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS pages_ai AFTER INSERT ON pages BEGIN
  INSERT INTO page_fts(rowid, text, document_id, page_no)
  VALUES (new.id, new.text, new.document_id, new.page_no);
END;

CREATE TRIGGER IF NOT EXISTS pages_ad AFTER DELETE ON pages BEGIN
  INSERT INTO page_fts(page_fts, rowid, text, document_id, page_no)
  VALUES('delete', old.id, old.text, old.document_id, old.page_no);
END;

CREATE TRIGGER IF NOT EXISTS pages_au AFTER UPDATE ON pages BEGIN
  INSERT INTO page_fts(page_fts, rowid, text, document_id, page_no)
  VALUES('delete', old.id, old.text, old.document_id, old.page_no);
  INSERT INTO page_fts(rowid, text, document_id, page_no)
  VALUES (new.id, new.text, new.document_id, new.page_no);
END;

CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS document_tags (
  document_id INTEGER NOT NULL,
  tag_id INTEGER NOT NULL,
  PRIMARY KEY(document_id, tag_id),
  FOREIGN KEY(document_id) REFERENCES documents(id),
  FOREIGN KEY(tag_id) REFERENCES tags(id)
);
