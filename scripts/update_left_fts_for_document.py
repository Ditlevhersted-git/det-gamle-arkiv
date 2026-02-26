import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "app" / "app.db"

def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def main():
    import sys
    if len(sys.argv) != 2:
        print("Usage: python scripts/update_left_fts_for_document.py <document_id>")
        raise SystemExit(2)

    document_id = int(sys.argv[1])

    con = connect()
    cur = con.cursor()

    rows = cur.execute("""
        SELECT id, left_search_text_v2
        FROM pages
        WHERE document_id = ?
        ORDER BY id
    """, (document_id,)).fetchall()

    updated = 0
    skipped = 0

    for r in rows:
        page_id = int(r["id"])
        txt = (r["left_search_text_v2"] or "").strip()

        # Sørg for at vi ikke har en gammel række
        cur.execute("DELETE FROM left_fts WHERE rowid = ?", (page_id,))

        # Kun indsæt hvis vi faktisk har tekst
        if txt:
            cur.execute(
                "INSERT INTO left_fts(rowid, left_search_text) VALUES(?, ?)",
                (page_id, txt),
            )
            updated += 1
        else:
            skipped += 1

    con.commit()
    con.close()

    print(f"FTS updated for document_id={document_id}: inserted={updated}, skipped_empty={skipped}")

if __name__ == "__main__":
    main()
