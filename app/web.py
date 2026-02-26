import io
import re
import json
import sqlite3
import sys
import subprocess
import tempfile
import time
from pathlib import Path

from flask import Flask, request, render_template_string, send_file, abort, Response, stream_with_context, redirect

DB_PATH = Path("app/app.db")
THUMBS_DIR = Path("data/thumbs")

app = Flask(__name__)

HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Arkiv søg</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; }
    input { width: 520px; padding: 10px; font-size: 16px; }
    button { padding: 10px 14px; font-size: 16px; }
    .muted { color: #666; margin-top: 10px; }
    .hit { display: flex; gap: 18px; padding: 16px 0; border-bottom: 1px solid #ddd; }
    img { width: 320px; height: auto; border: 1px solid #ccc; }
    .meta { font-size: 14px; width: 100%; }
    .title { font-weight: 800; font-size: 18px; color: #111; line-height: 1.2; }
    .extra { margin-top: 6px; color: #444; font-size: 14px; line-height: 1.25; }
    .footerline { margin-top: 10px; font-size: 13px; color: #666; }
    .actions { margin-top: 10px; display: flex; gap: 10px; align-items: center; }
    .actions a {
      display: inline-block; padding: 8px 12px;
      border: 1px solid #ccc; border-radius: 8px;
      text-decoration: none; color: #111;
    }
    .actions a:hover { background: #f3f3f3; }
    .actions form { display: inline; margin: 0; }
    .actions button {
      padding: 8px 12px;
      border: 1px solid #ccc; border-radius: 8px;
      background: white; color: #111; cursor: pointer;
      font-size: 14px;
    }
    .actions button:hover { background: #f3f3f3; }
    .toplinks { display:flex; gap:10px; align-items:center; }
  </style>
</head>
<body>
  <h2>Arkiv søg</h2>
  <form method="get" action="/" class="toplinks">
    <input name="q" value="{{q}}" placeholder="Søg i titel, forfatter, sted eller nr…" autofocus />
    <button type="submit">Søg</button>
    <a href="/" class="btn">Vis alle</a>
    <a href="/import" class="btn">Importér PDF</a>
  </form>

  {% if note %}<div class="muted">{{note}}</div>{% endif %}

  {% for h in hits %}
    <div class="hit">
      <div>
        <img src="/thumb/{{h['thumb']}}" alt="thumb">
      </div>
      <div class="meta">
        <div class="title">
          {{h['title_main']}}
          {% if h.get('nr') %} — {{h['nr']}}{% endif %}
          {% if h.get('scale') %} {{h['scale']}}{% endif %}
        </div>

        {% if h.get('title_extras') %}
          <div class="extra">
            {% for t in h['title_extras'] %}
              <div>{{t}}</div>
            {% endfor %}
          </div>
        {% endif %}

        <div class="actions">
          <a href="/open/{{h['page_id']}}" target="_blank" rel="noopener">Åbn</a>
          <a href="/download/{{h['page_id']}}">Download</a>

          <form method="post" action="/delete/{{h['page_id']}}" onsubmit="return confirm('Slet denne side permanent?');">
            <button type="submit">Slet</button>
          </form>
        </div>

        <div class="footerline">
          {{h['filename']}} — side {{h['page_no']}}
        </div>
      </div>
    </div>
  {% endfor %}
</body>
</html>
"""

IMPORT_HTML = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>Importér PDF</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 24px; }
    .box { border: 1px solid #ddd; border-radius: 10px; padding: 16px; max-width: 760px; }
    button { padding: 10px 14px; font-size: 16px; margin-top: 12px; }
    .muted { color:#666; margin-top: 10px; }
    a { display:inline-block; margin-left: 12px; }
  </style>
</head>
<body>
  <h2>Importér PDF</h2>
  <div class="box">
    <form method="post" enctype="multipart/form-data">
      <div>
        <input type="file" name="pdf" accept="application/pdf" required>
      </div>
      <div>
        <button type="submit">Importér</button>
        <a href="/">Tilbage</a>
      </div>
      <div class="muted">
        OBS: PDF skal være i originalt format (2 sider pr. side).
        Import kan tage nogle minutter, da siderne skal scannes.
      </div>
    </form>
  </div>
</body>
</html>
"""

# ---------- helpers ----------

def connect_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def project_root() -> Path:
    # app/web.py -> app/ -> project root
    return Path(__file__).resolve().parents[1]

def normalize(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-zæøå0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def build_fts_query(user_q: str, prefix: bool = True) -> str:
    q = normalize(user_q)
    toks = [t for t in q.split(" ") if t]
    if not toks:
        return ""
    safe = []
    for t in toks:
        t = re.sub(r"[^a-zæøå0-9]", "", t)
        if not t:
            continue
        safe.append(t + "*" if prefix else t)
    return " AND ".join(safe)

def _parse_titles_json(s):
    if not s:
        return []
    try:
        v = json.loads(s)
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
    except Exception:
        pass
    return []

def _label_from_row(r):
    # v2 først
    titles = _parse_titles_json(r["left_titles_json_v2"] if "left_titles_json_v2" in r.keys() else None)
    nr = (r["left_nr_v2"] if "left_nr_v2" in r.keys() else None) or ""
    scale = (r["left_scale_v2"] if "left_scale_v2" in r.keys() else None) or ""

    # fallback v1
    if not titles:
        titles = _parse_titles_json(r["left_titles_json"] if "left_titles_json" in r.keys() else None)
    if not nr and "left_nr" in r.keys():
        nr = r["left_nr"] or ""
    if not scale and "left_scale" in r.keys():
        scale = r["left_scale"] or ""

    titles = [t for t in titles if t]
    if not titles:
        titles = ["(mangler titel)"]
        # Ensartet visning: tilføj "Nr." hvis LLM kun gemte tal/tekst uden prefix
    if nr:
        nr_clean = nr.strip()
        if nr_clean and not re.match(r"(?i)^nr\.?\s*", nr_clean):
            nr_clean = nr_clean.lstrip(" .-")  # undgå "Nr. .104"
            nr = f"Nr. {nr_clean}"

    return titles[0], titles[1:], (nr.strip() or None), (scale.strip() or None)

def _safe_filename(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"[^\wæøåÆØÅ0-9\s\-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:120] if s else "side")

def _get_page_info(con, page_id: int):
    return con.execute("""
        SELECT
          p.id AS page_id,
          p.page_no,
          p.thumb_path,
          d.filename,
          d.path AS pdf_path,
          p.left_titles_json_v2,
          p.left_titles_json,
          p.left_nr_v2,
          p.left_nr,
          p.left_scale_v2,
          p.left_scale
        FROM pages p
        JOIN documents d ON d.id = p.document_id
        WHERE p.id = ?
    """, (page_id,)).fetchone()

def _first_title_from_page_row(r):
    titles = _parse_titles_json(r["left_titles_json_v2"] if "left_titles_json_v2" in r.keys() else None)
    if titles:
        return titles[0]
    titles = _parse_titles_json(r["left_titles_json"] if "left_titles_json" in r.keys() else None)
    return titles[0] if titles else None

def _download_name_from_page_row(r):
    t = _first_title_from_page_row(r)
    base = _safe_filename(t) if t else Path(r["filename"]).stem
    return f"{base}.pdf"

def _make_single_page_pdf(pdf_path: str, page_no_1based: int) -> bytes:
    import fitz  # PyMuPDF
    doc = fitz.open(pdf_path)
    idx = max(0, int(page_no_1based) - 1)
    if idx >= doc.page_count:
        doc.close()
        raise ValueError("Ugyldigt sidetal")
    out = fitz.open()
    out.insert_pdf(doc, from_page=idx, to_page=idx)
    data = out.tobytes()
    out.close()
    doc.close()
    return data

# ---------- queries ----------

def left_fts_search(con, fts_q: str, limit: int = 200):
    sql = """
    SELECT
      p.id AS page_id,
      d.filename,
      p.page_no,
      p.thumb_path,
      p.left_titles_json_v2,
      p.left_titles_json,
      p.left_nr_v2,
      p.left_nr,
      p.left_scale_v2,
      p.left_scale
    FROM left_fts
    JOIN pages p ON p.id = left_fts.rowid
    JOIN documents d ON d.id = p.document_id
    WHERE left_fts MATCH ?
    LIMIT ?;
    """
    return con.execute(sql, (fts_q, limit)).fetchall()

def left_substring_search(con, q: str, limit: int = 200):
    qn = normalize(q)
    if not qn:
        return []
    sql = """
    SELECT
      p.id AS page_id,
      d.filename,
      p.page_no,
      p.thumb_path,
      p.left_titles_json_v2,
      p.left_titles_json,
      p.left_nr_v2,
      p.left_nr,
      p.left_scale_v2,
      p.left_scale
    FROM pages p
    JOIN documents d ON d.id = p.document_id
    WHERE COALESCE(p.left_search_text_v2,'') <> ''
      AND instr(lower(p.left_search_text_v2), ?) > 0
    LIMIT ?;
    """
    return con.execute(sql, (qn, limit)).fetchall()

def all_pages(con, limit: int = 5000):
    return con.execute("""
        SELECT
          p.id AS page_id,
          d.filename,
          p.page_no,
          p.thumb_path,
          p.left_titles_json_v2,
          p.left_titles_json,
          p.left_nr_v2,
          p.left_nr,
          p.left_scale_v2,
          p.left_scale
        FROM pages p
        JOIN documents d ON d.id = p.document_id
        LIMIT ?;
    """, (limit,)).fetchall()

# ---------- routes ----------

@app.route("/")
def index():
    q = (request.args.get("q") or "").strip()
    hits = []
    note = None

    con = connect_db()

    # Browse-mode: q tom -> vis alle, men sorter alfabetisk efter titel
    if not q:
        rows = all_pages(con, limit=5000)
        for r in rows:
            main, extras, nr, scale = _label_from_row(r)
            hits.append({
                "page_id": r["page_id"],
                "filename": r["filename"],
                "page_no": r["page_no"],
                "thumb": Path(r["thumb_path"]).name,
                "title_main": main,
                "title_extras": extras,
                "nr": nr,
                "scale": scale,
            })
        con.close()

        hits.sort(key=lambda h: (normalize(h["title_main"]), normalize(h["filename"]), int(h["page_no"])))

        note = f"Resultater: {len(hits)}"
        return render_template_string(HTML, q=q, hits=hits, note=note)

    # Search-mode: bevar relevans (FTS først, derefter substring)
    seen = set()

    fts_q = build_fts_query(q, prefix=True)
    rows = []
    if fts_q:
        try:
            rows = left_fts_search(con, fts_q, limit=200)
        except sqlite3.OperationalError:
            rows = []

    for r in rows:
        if r["page_id"] in seen:
            continue
        seen.add(r["page_id"])
        main, extras, nr, scale = _label_from_row(r)
        hits.append({
            "page_id": r["page_id"],
            "filename": r["filename"],
            "page_no": r["page_no"],
            "thumb": Path(r["thumb_path"]).name,
            "title_main": main,
            "title_extras": extras,
            "nr": nr,
            "scale": scale,
        })

    if len(hits) < 30:
        rows2 = left_substring_search(con, q, limit=200)
        for r in rows2:
            if r["page_id"] in seen:
                continue
            seen.add(r["page_id"])
            main, extras, nr, scale = _label_from_row(r)
            hits.append({
                "page_id": r["page_id"],
                "filename": r["filename"],
                "page_no": r["page_no"],
                "thumb": Path(r["thumb_path"]).name,
                "title_main": main,
                "title_extras": extras,
                "nr": nr,
                "scale": scale,
            })

    con.close()
    note = f"Resultater: {len(hits)}" if hits else "Ingen resultater"
    return render_template_string(HTML, q=q, hits=hits, note=note)

@app.route("/thumb/<path:fname>")
def thumb(fname):
    path = (THUMBS_DIR / Path(fname).name).resolve()
    if not path.exists():
        abort(404)
    return send_file(str(path))

@app.route("/view/<int:page_id>")
def view_page(page_id: int):
    con = connect_db()
    r = _get_page_info(con, page_id)
    con.close()
    if not r:
        abort(404)

    try:
        pdf_bytes = _make_single_page_pdf(r["pdf_path"], r["page_no"])
    except Exception as e:
        abort(500, description=str(e))

    fname = _download_name_from_page_row(r)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=fname
    )

@app.route("/download/<int:page_id>")
def download_page(page_id: int):
    con = connect_db()
    r = _get_page_info(con, page_id)
    con.close()
    if not r:
        abort(404)

    try:
        pdf_bytes = _make_single_page_pdf(r["pdf_path"], r["page_no"])
    except Exception as e:
        abort(500, description=str(e))

    fname = _download_name_from_page_row(r)
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=fname
    )

@app.route("/open/<int:page_id>")
def open_page(page_id: int):
    con = connect_db()
    r = _get_page_info(con, page_id)
    con.close()
    if not r:
        abort(404)

    title = (_first_title_from_page_row(r) or Path(r["filename"]).stem).strip()
    title = title.replace("<", "").replace(">", "")

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <style>
    html, body {{ height: 100%; margin: 0; }}
    iframe {{ width: 100%; height: 100%; border: 0; }}
  </style>
</head>
<body>
  <iframe src="/view/{page_id}"></iframe>
</body>
</html>
"""
    return html

@app.route("/delete/<int:page_id>", methods=["POST"])
def delete_page(page_id: int):
    con = connect_db()
    r = _get_page_info(con, page_id)
    if not r:
        con.close()
        abort(404)

    thumb_name = Path(r["thumb_path"]).name if r["thumb_path"] else None

    try:
        try:
            con.execute("DELETE FROM left_fts WHERE rowid = ?", (page_id,))
        except sqlite3.OperationalError:
            pass

        con.execute("DELETE FROM pages WHERE id = ?", (page_id,))
        con.commit()
    finally:
        con.close()

    if thumb_name:
        p = (THUMBS_DIR / thumb_name).resolve()
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass

    return redirect("/")

# ---------- import ----------

@app.route("/import", methods=["GET", "POST"])
def import_pdf():
    if request.method == "GET":
        return render_template_string(IMPORT_HTML)

    f = request.files.get("pdf")
    if not f or not f.filename:
        abort(400, description="Ingen fil valgt")

    orig_name = Path(f.filename).name
    orig_name = re.sub(r"[^A-Za-z0-9ÆØÅæøå _\-.]", "_", orig_name)
    if not orig_name.lower().endswith(".pdf"):
        orig_name += ".pdf"

    tmp_dir = Path(tempfile.gettempdir())
    tmp_path = tmp_dir / orig_name

    i = 2
    while tmp_path.exists():
        tmp_path = tmp_dir / f"{Path(orig_name).stem}_{i}.pdf"
        i += 1

    f.save(str(tmp_path))

    root = project_root()
    py = sys.executable

    def run_cmd(cmd):
        p = subprocess.Popen(
            cmd,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        try:
            for line in p.stdout:
                yield line.rstrip("\n")
        finally:
            p.wait()
        if p.returncode != 0:
            raise RuntimeError(f"Command failed ({p.returncode}): {' '.join(cmd)}")

    @stream_with_context
    def stream():
        start_ts = time.time()

        yield """<!doctype html><html><head><meta charset="utf-8"/>
<title>Import i gang…</title>
<style>
body{font-family:Arial,sans-serif;margin:24px;}
.status{font-size:22px;font-weight:800;margin-bottom:10px;}
.small{color:#666;margin-bottom:14px;}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace;background:#f7f7f7;padding:10px;border-radius:8px;white-space:pre-wrap;display:none;}
</style>
</head><body>
<div class="status" id="status">Importering igang</div>
<div class="small" id="small">Vent venligst… Dette kan tage nogle minutter.</div>
<div class="mono" id="log"></div>
<script>
function setStatus(t){document.getElementById('status').textContent=t;}
function setSmall(t){document.getElementById('small').textContent=t;}
function addLog(t){
  var el = document.getElementById('log');
  el.style.display = 'block';
  el.textContent += t + "\\n";
}
</script>
"""

        document_id = None

        try:
            ingest_cmd = [py, "scripts/ingest_pdf.py", str(tmp_path)]
            for line in run_cmd(ingest_cmd):
                if line.startswith("DOCUMENT_ID="):
                    document_id = int(line.split("=", 1)[1].strip())
                    continue

                if line.startswith("THUMB "):
                    prog = line.split(" ", 1)[1].strip()  # fx "1/65"
                    yield f"<script>setStatus('Genererer billeder (side {prog})');</script>\n"
                    continue

                # ellers: ignorer rå log-linjer

            if document_id is None:
                yield "<script>setStatus('Fejl'); addLog('ERROR: ingest gav ingen DOCUMENT_ID – stopper.');</script>\n"
                yield "<p><a href='/import'>Tilbage</a></p>\n"
                yield "</body></html>"
                return

            con = connect_db()
            con.execute("""
                UPDATE pages
                SET left_source_v2 = NULL
                WHERE document_id = ?
                  AND left_source_v2 LIKE 'llm_error_v2:%'
            """, (document_id,))
            con.commit()
            con.close()

            yield "<script>setStatus('Scanner venstre labels…');</script>\n"
            llm_cmd = [py, "scripts/llm_left_labels_v2.py", "--document-id", str(document_id)]

            # --- DEBUG ADDITION (isolated): capture full LLM output on failure ---
            llm_out = []
            llm_p = subprocess.Popen(
                llm_cmd,
                cwd=str(root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            try:
                for line in llm_p.stdout:
                    line = line.rstrip("\n")
                    llm_out.append(line)

                    m = re.match(r"^\[(\d+)/(\d+)\]\s+page_id=", line)
                    if m:
                        i, n = m.group(1), m.group(2)
                        yield f"<script>setStatus('Scanner venstre labels (side {i}/{n})');</script>\n"
                        continue

                    if "ERROR" in line or "Traceback" in line:
                        safe = line.replace("\\", "\\\\").replace("'", "\\'")
                        yield f"<script>addLog('{safe}');</script>\n"
            finally:
                llm_p.wait()

            if llm_p.returncode != 0:
                out_text = "\n".join(llm_out) if llm_out else f"(no output) returncode={llm_p.returncode}"
                safe = out_text.replace("\\", "\\\\").replace("'", "\\'").replace("\r", "").replace("\n", "\\n")
                yield "<script>setStatus('Fejl');</script>\n"
                yield f"<script>addLog('{safe}');</script>\n"
                yield "<p><a href='/import'>Tilbage</a></p>\n"
                return
            # --- end debug addition ---

            yield "<script>setStatus('Opdaterer arkiv…');</script>\n"
            fts_cmd = [py, "scripts/update_left_fts_for_document.py", str(document_id)]
            for _ in run_cmd(fts_cmd):
                pass

            elapsed = int(time.time() - start_ts)
            mm = elapsed // 60
            ss = elapsed % 60
            took = f"{mm}:{ss:02d}"

            yield "<script>setStatus('Importering er nu færdig ✅');</script>\n"
            yield f"<script>setSmall('Tid i alt: {took}');</script>\n"
            yield "<p><a href='/'>Gå tilbage til oversigten</a></p>\n"

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            safe = tb.replace("\\", "\\\\").replace("'", "\\'").replace("\r", "").replace("\n", "\\n")
            yield "<script>setStatus('Fejl');</script>\n"
            yield f"<script>addLog('{safe}');</script>\n"
            yield "<p><a href='/import'>Tilbage</a></p>\n"

        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

        yield "</body></html>"

    return Response(stream(), mimetype="text/html")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)