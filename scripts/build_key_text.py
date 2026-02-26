import sqlite3
import subprocess
from pathlib import Path
from PIL import Image

DB_PATH = Path("app/app.db")
PDF_PATH = Path("data/processed/+ Modeludvalg  gamle 98 - 160.pdf")
TMP_DIR = Path("data/tmp_key")
TMP_DIR.mkdir(parents=True, exist_ok=True)

def render_page(pdf_path: Path, page_no: int, out_png: Path):
    # render hele siden
    prefix = out_png.with_suffix("")
    cmd = [
        "pdftoppm", "-png", "-r", "200",
        "-f", str(page_no), "-l", str(page_no),
        "-singlefile",
        str(pdf_path), str(prefix)
    ]
    subprocess.run(cmd, check=True)

def ocr_image(img_path: Path) -> str:
    # OCR på dansk + engelsk (dansk hjælper på æøå)
    cmd = ["tesseract", str(img_path), "stdout", "-l", "dan+eng", "--psm", "6"]
    res = subprocess.run(cmd, check=True, capture_output=True, text=True)
    text = " ".join(res.stdout.split())
    return text.strip()

def main():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    # hent pages for doc_id=1 (tilpas senere når du har flere docs)
    rows = con.execute("""
        SELECT id, page_no, document_id
        FROM pages
        WHERE document_id = 1
        ORDER BY page_no
    """).fetchall()

    for r in rows:
        pid = r["id"]
        page_no = r["page_no"]

        full_png = TMP_DIR / f"p{page_no}_full.png"
        render_page(PDF_PATH, page_no, full_png)

        img = Image.open(full_png)
        w, h = img.size

        # crop: venstre halvdel + kun top 35% (overskrift område)
        crop = img.crop((0, 0, int(w*0.5), h))
        crop_png = TMP_DIR / f"p{page_no}_lefttop.png"
        crop.save(crop_png)

        key_text = ocr_image(crop_png)

        con.execute("UPDATE pages SET key_text=? WHERE id=?", (key_text, pid))

        if page_no % 10 == 0:
            con.commit()
            print(f"{page_no}/{rows[-1]['page_no']}")

    con.commit()
    con.close()
    print("Done build_key_text")

if __name__ == "__main__":
    main()
