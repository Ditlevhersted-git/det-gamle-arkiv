import sqlite3
import subprocess
from pathlib import Path
from pypdf import PdfReader

DB_PATH = Path("app/app.db")
THUMBS_DIR = Path("data/thumbs")

PDF_PATH = Path("data/processed/+ Modeludvalg  gamle 98 - 160.pdf")

def ensure_document(con: sqlite3.Connection, pdf_path: Path) -> int:
    pdf_path = pdf_path.resolve()
    con.execute(
        "INSERT OR IGNORE INTO documents(path, filename) VALUES (?, ?)",
        (str(pdf_path), pdf_path.name),
    )
    (doc_id,) = con.execute(
        "SELECT id FROM documents WHERE path=?",
        (str(pdf_path),),
    ).fetchone()
    return int(doc_id)

def make_thumb(pdf_path: Path, doc_id: int, page_no: int) -> str:
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    out_prefix = THUMBS_DIR / f"doc{doc_id}_p{page_no}"
    out_png = f"{out_prefix}.png"
    if Path(out_png).exists():
        return out_png

    cmd = [
        "pdftoppm",
        "-png",
        "-r", "140",
        "-f", str(page_no),
        "-l", str(page_no),
        "-singlefile",
        str(pdf_path),
        str(out_prefix),
    ]
    subprocess.run(cmd, check=True)
    return out_png

def main():
    if not PDF_PATH.exists():
        raise SystemExit(f"Missing OCR PDF: {PDF_PATH}")

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys=ON;")

    doc_id = ensure_document(con, PDF_PATH)

    reader = PdfReader(str(PDF_PATH))
    total = len(reader.pages)
    print(f"Ingesting: {PDF_PATH.name} | pages={total} | doc_id={doc_id}")

    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = " ".join(text.split())

        thumb = make_thumb(PDF_PATH, doc_id, i)

        con.execute(
            """
            INSERT OR REPLACE INTO pages(document_id, page_no, text, thumb_path)
            VALUES (?, ?, ?, ?)
            """,
            (doc_id, i, text, thumb),
        )

        if i % 10 == 0 or i == total:
            con.commit()
            print(f"  {i}/{total}")

    con.commit()
    con.close()
    print("Done.")

if __name__ == "__main__":
    main()
