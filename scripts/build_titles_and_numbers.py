import sqlite3, re
from pathlib import Path
from PIL import Image, ImageOps
import pytesseract

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "app" / "app.db"

def resolve_thumb(p):
    if not p:
        return None
    pp = Path(p)
    return pp if pp.is_absolute() else (ROOT / pp).resolve()

def ensure_columns(con):
    cols = [r[1] for r in con.execute("PRAGMA table_info(documents);")]
    if "title" not in cols:
        con.execute("ALTER TABLE documents ADD COLUMN title TEXT;")
    if "model_no" not in cols:
        con.execute("ALTER TABLE documents ADD COLUMN model_no TEXT;")
    con.commit()

def extract_model_no_from_thumb(thumb_path):
    im = Image.open(thumb_path).convert("L")
    im = ImageOps.autocontrast(im)
    w, h = im.size
    r = im.crop((int(w*0.43), int(h*0.74), int(w*0.50), int(h*0.92)))
    r = r.resize((r.size[0]*6, r.size[1]*6))
    t = pytesseract.image_to_string(r, lang="dan+eng", config="--psm 6")
    nums = re.findall(r"\d{2,4}", t)
    return nums[0] if nums else None

def clean_piece(s):
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" .,_-:;|")
    return s

def split_key_text(key_text):
    # key_text bruger ofte '|' som pseudo-linjeskift
    raw = (key_text or "")
    raw = raw.replace("\r", "\n")
    raw = raw.replace("|", "\n")
    parts = [clean_piece(p) for p in raw.split("\n")]
    return [p for p in parts if p and len(p) >= 6]

def extract_title_from_key_text(key_text):
    parts = split_key_text(key_text)
    top = parts[:40]  # vi kigger kun "øverst"

    # 1) bedste: første der indeholder "ved"
    for p in top:
        if "ved" in p.lower():
            # stop ved dobbelt-mellemrum eller tydelig støj senere
            return p

    # 2) fallback: mest bogstav-tæt og få tal
    best, best_score = None, -999
    for p in top:
        letters = len(re.findall(r"[a-zæøå]", p.lower()))
        digits = len(re.findall(r"\d", p))
        score = letters - 3 * digits
        if score > best_score:
            best_score = score
            best = p

    return best

def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    ensure_columns(con)

    rows = con.execute("""
        SELECT d.id, d.filename, p.thumb_path, p.key_text
        FROM documents d
        JOIN pages p ON p.document_id = d.id
        WHERE p.page_no = (
            SELECT MIN(p2.page_no) FROM pages p2 WHERE p2.document_id = d.id
        )
    """).fetchall()

    updated = 0
    for r in rows:
        title = extract_title_from_key_text(r["key_text"])

        thumb = resolve_thumb(r["thumb_path"])
        model_no = extract_model_no_from_thumb(thumb) if thumb and thumb.exists() else None

        if title or model_no:
            con.execute(
                "UPDATE documents SET title=?, model_no=? WHERE id=?",
                (title, model_no, r["id"])
            )
            updated += 1

    con.commit()
    con.close()
    print(f"Updated {updated}/{len(rows)}")

if __name__ == "__main__":
    main()
