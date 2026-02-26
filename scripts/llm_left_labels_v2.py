import argparse
import base64
import io
import json
import re
import sqlite3
import time
from pathlib import Path

from openai import OpenAI
from PIL import Image

DB = Path("app/app.db")
client = OpenAI()

PROMPT = r"""
Du får et billede af en scannet side (opslag).
DU MÅ KUN bruge VENSTRE side (tegning + overskrifter + nr nederst).
Ignorér højre side tekst fuldstændigt.

Opgave:
A) Find ALLE tydelige overskrifter på venstre side (typisk 2-5).
   - Returnér dem i læseorden (top → bund).
B) Find "Nr." nederst.
C) Find målestok(e).
D) Du må gerne gætte hvis næsten læsbart — men sæt confidence lavere.

SVAR KUN MED JSON:
{"titles":["..."],"nr":"...","scale":"...","confidence":0.0}
""".strip()

MAX_RETRIES = 6
BASE_SLEEP = 2
MAX_UPLOAD_BYTES = 900_000


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def resolve_thumb(thumb_path: str) -> Path | None:
    if not thumb_path:
        return None
    p = Path(thumb_path)
    if p.exists():
        return p.resolve()
    alt = Path("data/thumbs") / p.name
    if alt.exists():
        return alt.resolve()
    return None


def compress_for_llm(path: Path) -> str:
    raw = path.read_bytes()
    img = Image.open(io.BytesIO(raw)).convert("RGB")

    max_w = 1400
    if img.width > max_w:
        ratio = max_w / img.width
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)

    q = 70
    while True:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=q, optimize=True)
        data = buf.getvalue()
        if len(data) <= MAX_UPLOAD_BYTES or q <= 35:
            b64 = base64.b64encode(data).decode("ascii")
            return f"data:image/jpeg;base64,{b64}"
        q -= 7


def extract_json(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("{") and t.endswith("}"):
        return json.loads(t)
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if not m:
        raise ValueError("No JSON found")
    return json.loads(m.group(0))


def call_llm(image_data_url: str) -> dict:
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.responses.create(
                model="gpt-4.1-mini",
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": PROMPT},
                        {"type": "input_image", "image_url": image_data_url},
                    ],
                }],
            )

            out_text = getattr(resp, "output_text", None)
            if out_text:
                return extract_json(out_text)

            chunks = []
            for item in getattr(resp, "output", []) or []:
                for c in item.get("content", []):
                    if c.get("type") in ("output_text", "text"):
                        chunks.append(c.get("text", ""))
            return extract_json("\n".join(chunks))

        except Exception as e:
            last_error = e
            time.sleep(BASE_SLEEP * attempt)

    raise last_error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", type=int, default=None)
    args = parser.parse_args()

    con = connect()

    if args.document_id is None:
        rows = con.execute("""
            SELECT id, page_no, thumb_path
            FROM pages
            WHERE left_source_v2 IS NULL
            ORDER BY id
        """).fetchall()
        print(f"Pages to enrich (missing): {len(rows)}")
    else:
        rows = con.execute("""
            SELECT id, page_no, thumb_path
            FROM pages
            WHERE document_id = ?
              AND left_source_v2 IS NULL
            ORDER BY id
        """, (args.document_id,)).fetchall()
        print(f"Pages to enrich (missing, doc={args.document_id}): {len(rows)}")

    for i, r in enumerate(rows, 1):
        page_id = r["id"]
        page_no = r["page_no"]
        thumb = resolve_thumb(r["thumb_path"])

        if not thumb:
            print(f"[{i}/{len(rows)}] page_id={page_id} side={page_no} MISSING_THUMB")
            continue

        try:
            img_url = compress_for_llm(thumb)
            out = call_llm(img_url)

            titles = out.get("titles") or []
            nr = (out.get("nr") or "").strip()
            scale = (out.get("scale") or "").strip()
            conf = float(out.get("confidence") or 0.0)

            cleaned = []
            seen = set()
            for t in titles:
                t = str(t).strip()
                if not t:
                    continue
                t = t[:90]
                key = t.lower()
                if key in seen:
                    continue
                seen.add(key)
                cleaned.append(t)

            # ✅ TRIN 2: byg search blob til substring fallback + FTS source
            search_blob = " ".join([*cleaned[:5], nr, scale]).strip()

            con.execute("""
                UPDATE pages
                SET left_titles_json_v2=?,
                    left_nr_v2=?,
                    left_scale_v2=?,
                    left_confidence_v2=?,
                    left_source_v2=?,
                    left_search_text_v2=?
                WHERE id=?
            """, (
                json.dumps(cleaned[:5], ensure_ascii=False),
                nr,
                scale,
                conf,
                "llm:v2:missing",
                search_blob,
                page_id
            ))
            con.commit()

            print(f"[{i}/{len(rows)}] page_id={page_id} OK conf={conf:.2f}")

        except Exception as e:
            con.execute("""
                UPDATE pages
                SET left_source_v2=?
                WHERE id=?
            """, (f"llm_error_v2:{type(e).__name__}", page_id))
            con.commit()

            print(f"[{i}/{len(rows)}] page_id={page_id} ERROR: {e}")

    con.close()


if __name__ == "__main__":
    main()
