import base64
import json
import os
import sqlite3
from pathlib import Path

import fitz  # PyMuPDF
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "app" / "app.db"

MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
client = OpenAI()

PROMPT = """
Du får et scan af EN PDF-side (opslag). Brug KUN venstre side.
Udtræk KUN:
1) overskrift/overskrifter (typisk øverst og evt. en ekstra titel nederst)
2) nummer nederst (fx 'Nr. 135')
3) skala nederst (fx '1:2')

Regler:
- Gæt ikke. Hvis uklart, returnér tom streng og lav confidence.
- Returnér KUN JSON med felterne:
  titles (liste af str),
  nr (str),
  scale (str),
  confidence (tal 0-1)
"""

def render_left_half_png(pdf_path: str, page_no_1based: int, zoom: float = 2.0) -> bytes:
    doc = fitz.open(pdf_path)
    try:
        idx = page_no_1based - 1
        if idx < 0 or idx >= doc.page_count:
            raise ValueError("Ugyldigt sidetal")

        page = doc.load_page(idx)
        rect = page.rect
        left_rect = fitz.Rect(rect.x0, rect.y0, rect.x0 + rect.width / 2, rect.y1)

        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, clip=left_rect, alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()

def llm_extract(image_png_bytes: bytes) -> dict:
    b64 = base64.b64encode(image_png_bytes).decode("utf-8")
    resp = client.responses.create(
        model=MODEL,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": PROMPT},
                {"type": "input_image", "image_url": f"data:image/png;base64,{b64}"},
            ],
        }],
        text={"format": {"type": "json_object"}},
    )
    return json.loads(resp.output_text)

def ensure_columns(con: sqlite3.Connection):
    # Tilføj kolonner hvis de mangler (gør scriptet mere robust)
    existing = {r[1] for r in con.execute("PRAGMA table_info(pages);").fetchall()}
    wanted = {
        "left_titles_json": "TEXT",
        "left_nr": "TEXT",
        "left_scale": "TEXT",
        "left_confidence": "REAL",
        "left_source": "TEXT",
    }
    for col, typ in wanted.items():
        if col not in existing:
            con.execute(f"ALTER TABLE pages ADD COLUMN {col} {typ};")
    con.commit()

def main():
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY er ikke sat i miljøet.")

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    ensure_columns(con)

    rows = con.execute("""
        SELECT
          p.id AS page_id,
          p.page_no,
          d.path AS pdf_path
        FROM pages p
        JOIN documents d ON d.id = p.document_id
        WHERE COALESCE(p.left_source,'') = ''
        ORDER BY p.id ASC
    """).fetchall()

    print(f"Pages to enrich: {len(rows)}")

    for i, r in enumerate(rows, 1):
        page_id = r["page_id"]
        page_no = r["page_no"]
        pdf_path = r["pdf_path"]

        try:
            img = render_left_half_png(pdf_path, page_no, zoom=2.0)
            out = llm_extract(img)

            titles = out.get("titles") or []
            if not isinstance(titles, list):
                titles = [str(titles)]

            nr = (out.get("nr") or "").strip()
            scale = (out.get("scale") or "").strip()

            try:
                conf = float(out.get("confidence") or 0.0)
            except Exception:
                conf = 0.0

            con.execute("""
                UPDATE pages
                SET left_titles_json = ?,
                    left_nr = ?,
                    left_scale = ?,
                    left_confidence = ?,
                    left_source = ?
                WHERE id = ?
            """, (
                json.dumps(titles, ensure_ascii=False),
                nr,
                scale,
                conf,
                f"llm:{MODEL}:left_v1",
                page_id,
            ))
            con.commit()

            print(f"[{i}/{len(rows)}] page_id={page_id} ok conf={conf:.2f} nr='{nr}' scale='{scale}' titles={len(titles)}")

        except Exception as e:
            # markér fejl så du kan rerun senere
            con.execute("UPDATE pages SET left_source=? WHERE id=?", (f"llm_error:{type(e).__name__}", page_id))
            con.commit()
            print(f"[{i}/{len(rows)}] page_id={page_id} ERROR: {e}")

    con.close()
    print("Done.")

if __name__ == "__main__":
    main()