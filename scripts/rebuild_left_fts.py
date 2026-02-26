import json
import sqlite3
from pathlib import Path

DB = Path("app/app.db")

def parse_titles(s):
    if not s:
        return []
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
    except Exception:
        pass
    return []

def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    rows = con.execute("""
        SELECT
          id,
          left_titles_json_v2,
          left_nr_v2,
          left_scale_v2
        FROM pages
    """).fetchall()

    # 1) udfyld left_search_text_v2
    updated = 0
    for r in rows:
        titles = parse_titles(r["left_titles_json_v2"])
        nr = (r["left_nr_v2"] or "").strip()
        scale = (r["left_scale_v2"] or "").strip()

        parts = []
        parts += titles
        if nr:
            parts.append(nr)
        if scale:
            parts.append(scale)

        text = " · ".join([p for p in parts if p]).strip()
        con.execute("UPDATE pages SET left_search_text_v2=? WHERE id=?", (text, r["id"]))
        updated += 1

    con.commit()

    # 2) rebuild left_fts contentless
    con.execute("DELETE FROM left_fts;")
    con.execute("""
        INSERT INTO left_fts(rowid, left_search_text)
        SELECT id, COALESCE(left_search_text_v2,'')
        FROM pages
        WHERE COALESCE(left_search_text_v2,'') <> '';
    """)
    con.commit()

    # sanity
    c_pages = con.execute("SELECT COUNT(*) FROM pages;").fetchone()[0]
    c_fts = con.execute("SELECT COUNT(*) FROM left_fts;").fetchone()[0]
    print(f"pages: {c_pages}")
    print(f"left_fts rows: {c_fts}")
    print(f"updated left_search_text_v2: {updated}")

    con.close()

if __name__ == "__main__":
    main()