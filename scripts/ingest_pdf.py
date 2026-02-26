import re
import shutil
import sqlite3
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "app" / "app.db"
PDFS_DIR = ROOT / "data" / "pdfs"
THUMBS_DIR = ROOT / "data" / "thumbs"

def connect_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def safe_stem(name: str) -> str:
    s = (name or "").strip()
    s = re.sub(r"\.pdf$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"[^\wæøåÆØÅ0-9\- ]+", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s[:80] if s else "doc"

def unique_pdf_path(dst_dir: Path, filename: str) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    base = safe_stem(Path(filename).name)
    p = dst_dir / f"{base}.pdf"
    if not p.exists():
        return p
    i = 2
    while True:
        cand = dst_dir / f"{base}_{i}.pdf"
        if not cand.exists():
            return cand
        i += 1

def main():
    import sys
    if len(sys.argv) != 2:
        print("Usage: python scripts/ingest_pdf.py /path/to/file.pdf", flush=True)
        raise SystemExit(2)

    src_pdf = Path(sys.argv[1]).expanduser()
    if not src_pdf.exists():
        print(f"ERROR: PDF not found: {src_pdf}", flush=True)
        raise SystemExit(1)

    PDFS_DIR.mkdir(parents=True, exist_ok=True)
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)

    dst_pdf = unique_pdf_path(PDFS_DIR, src_pdf.name)
    shutil.copy2(src_pdf, dst_pdf)

    doc = fitz.open(str(dst_pdf))
    total = doc.page_count

    con = connect_db()
    cur = con.cursor()

    cur.execute(
        "INSERT INTO documents(path, filename) VALUES(?, ?)",
        (str(dst_pdf), dst_pdf.name),
    )
    document_id = cur.lastrowid
    con.commit()

    print(f"DOCUMENT_ID={document_id}", flush=True)
    print(f"PAGES={total}", flush=True)

    page_ids = []
    for i in range(total):
        page_no_1 = i + 1
        cur.execute(
            "INSERT INTO pages(document_id, page_no, text, thumb_path) VALUES(?, ?, ?, ?)",
            (document_id, page_no_1, "", None),
        )
        page_ids.append(cur.lastrowid)
    con.commit()

    stem = safe_stem(dst_pdf.stem)
    for i, page_id in enumerate(page_ids):
        page_no_1 = i + 1
        page = doc.load_page(i)
        pix = page.get_pixmap(dpi=140)
        thumb_name = f"{stem}_p{page_no_1}.png"
        pix.save(str(THUMBS_DIR / thumb_name))

        rel_thumb = f"data/thumbs/{thumb_name}"
        cur.execute("UPDATE pages SET thumb_path=? WHERE id=?", (rel_thumb, page_id))
        con.commit()

        print(f"THUMB {page_no_1}/{total}", flush=True)

    doc.close()
    con.close()
    print("INGEST_DONE=1", flush=True)

if __name__ == "__main__":
    main()