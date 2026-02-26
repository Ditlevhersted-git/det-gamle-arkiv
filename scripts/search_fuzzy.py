import sqlite3
import sys
import re
from rapidfuzz import fuzz

DB_PATH = "app/app.db"

def normalize(s: str) -> str:
    s = s.lower()
    # fjern mærkelige tegn, behold danske bogstaver
    s = re.sub(r"[^a-zæøå0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def fts_hits(con, query: str, limit: int = 20):
    # FTS5 query: vi bruger raw query her. (Senere sanitiserer vi bedre i UI.)
    sql = """
    SELECT pages.id, pages.page_no, pages.text, pages.thumb_path
    FROM page_fts
    JOIN pages ON pages.id = page_fts.rowid
    WHERE page_fts MATCH ?
    LIMIT ?;
    """
    return con.execute(sql, (query, limit)).fetchall()

def fuzzy_scan(con, query: str, limit_pages: int = 300, top_k: int = 10):
    qn = normalize(query)
    # Hent en bunke sider (i starten kun doc_id=1 for simplicity)
    rows = con.execute("""
        SELECT id, page_no, text, thumb_path
        FROM pages
        WHERE document_id=1
        LIMIT ?;
    """, (limit_pages,)).fetchall()

    scored = []
    for (pid, page_no, text, thumb) in rows:
        tn = normalize(text)
        # Partial ratio er god til at finde "ord inde i længere tekst"
        score = fuzz.partial_ratio(qn, tn)
        if score >= 70:  # threshold; juster senere
            scored.append((score, page_no, pid, thumb, text))

    scored.sort(reverse=True, key=lambda x: x[0])
    return scored[:top_k]

def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/search_fuzzy.py <query>")
        sys.exit(1)

    query = " ".join(sys.argv[1:])

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys=ON;")

    # 1) Normal FTS (hurtig)
    hits = fts_hits(con, query, limit=10)

    if hits:
        print(f"FTS hits for: {query}")
        for pid, page_no, text, thumb in hits:
            snippet = (text[:160] + "...") if len(text) > 160 else text
            print(f"- page {page_no} | thumb={thumb}\n  {snippet}\n")
        return

    # 2) Fuzzy fallback (tolerant)
    print(f"No FTS hits. Fuzzy scanning for: {query}")
    scored = fuzzy_scan(con, query, limit_pages=1000, top_k=10)

    if not scored:
        print("No fuzzy hits (threshold too high or not present).")
        return

    for score, page_no, pid, thumb, text in scored:
        snippet = (text[:160] + "...") if len(text) > 160 else text
        print(f"- score {score} | page {page_no} | thumb={thumb}\n  {snippet}\n")

if __name__ == "__main__":
    main()