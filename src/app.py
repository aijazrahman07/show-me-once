"""Synthetic purchase-order / delivery reconciliation app.

Stdlib only. Serves two controlled layouts over one API and implements the
reviewed procedure in docs/PROCEDURE.md (observed rules O1-O5, gap
resolutions M1-M6).

Run:  python3 src/app.py [port]   (default 8000)

Agent extension (additive; receiving app untouched): Meta Model API
(muse-spark-1.3) powers POST /api/learn-from-recording (frames +
transcript -> procedures.json entry) and POST /api/execute-procedure
(Playwright agent loop, 10 turns max). Static JSON: /procedures.json,
/revision_history.json, /supervisor_queue.json.
"""
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "data", "fixtures")
TEMPLATES = os.path.join(ROOT, "src", "templates")
RECORDS_PATH = os.path.join(ROOT, "data", "records.json")
QUEUE_PATH = os.path.join(ROOT, "data", "supervisor_queue.json")
PO_PATH = os.path.join(FIXTURES, "purchase_orders.json")
DELIVERIES_PATH = os.path.join(FIXTURES, "deliveries.json")
PROCEDURES_PATH = os.path.join(ROOT, "data", "procedures.json")
REVISION_HISTORY_PATH = os.path.join(ROOT, "data", "revision_history.json")

META_API_URL = "https://api.meta.ai/v1/chat/completions"
META_MODEL_DEFAULT = "muse-spark-1.3"
MAX_TURNS = 10

STATUS_MATCH = "match"
STATUS_SHORTAGE = "Discrepancy\u2014shortage"
STATUS_OVERAGE = "Discrepancy\u2014overage"
STATUS_MISSING = "missing_quantity"

SUPERVISOR = "receiving-supervisor"
REVIEW_TARGET = "1 business day"
NOTE_MAX = 500


class ValidationError(Exception):
    pass


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_json_file(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json_file(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def load_records():
    """{deliveryId: {"current": {...}, "history": [...]}}; migrates old list shape."""
    data = load_json_file(RECORDS_PATH, {})
    if isinstance(data, dict):
        return data
    migrated = {}
    if isinstance(data, list):
        for r in data:
            did = r.get("deliveryId")
            if not did:
                continue
            r.setdefault("revision", 1)
            r.setdefault("resolved", r.get("status") == STATUS_MATCH)
            r.setdefault("discrepancy", None)
            r.setdefault("correctionReason", "")
            r.setdefault("previousValues", None)
            r.setdefault("actor", "operator")
            migrated[did] = {"current": r, "history": []}
    return migrated


def load_queue():
    data = load_json_file(QUEUE_PATH, [])
    return data if isinstance(data, list) else []


def parse_received(raw):
    """Whole units only (M4). Returns int, or None for blank (missing)."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return None
    if isinstance(raw, bool):
        raise ValidationError("receivedQuantity must be a whole number of units or blank")
    if isinstance(raw, int):
        qty = raw
    elif isinstance(raw, float):
        if not raw.is_integer():
            raise ValidationError(f"receivedQuantity {raw} is fractional; whole units only")
        qty = int(raw)
    elif isinstance(raw, str):
        if not re.fullmatch(r"-?\d+", raw.strip()):
            raise ValidationError(f"receivedQuantity {raw!r} is not numeric; whole units only")
        qty = int(raw.strip())
    else:
        raise ValidationError("receivedQuantity must be a whole number of units or blank")
    if qty < 0:
        raise ValidationError(f"receivedQuantity {qty} is negative; whole units only")
    return qty


def classify(ordered, received):
    """Returns (status, discrepancy, resolved)."""
    if received is None:
        return STATUS_MISSING, {"type": "missing_quantity"}, False
    if received == ordered:
        return STATUS_MATCH, None, True
    if received < ordered:
        return STATUS_SHORTAGE, {"type": "shortage", "missing": ordered - received}, False
    return STATUS_OVERAGE, {"type": "overage", "excess": received - ordered}, False


def get_meta_api_key():
    """META_API_KEY from env (MODEL_API_KEY accepted for back-compat), else .env."""
    for var in ("META_API_KEY", "MODEL_API_KEY"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        with open(os.path.join(ROOT, ".env"), encoding="utf-8") as f:
            file_vals = {}
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    file_vals[k.strip()] = v.strip()
    except FileNotFoundError:
        return ""
    return file_vals.get("META_API_KEY") or file_vals.get("MODEL_API_KEY") or ""


def strip_code_fences(text):
    """Remove one ```json / ``` wrapper so model output parses as JSON."""
    text = (text or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def call_spark(prompt, image_b64=None):
    """One Meta Model API chat call (muse-spark-1.3). Returns content string."""
    key = get_meta_api_key()
    if not key:
        raise RuntimeError("META_API_KEY is not set (checked environment and .env)")
    model = os.environ.get("META_MODEL", META_MODEL_DEFAULT)
    if image_b64:
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + image_b64}},
        ]
    else:
        content = prompt
    req = urllib.request.Request(
        META_API_URL,
        data=json.dumps({"model": model, "messages": [{"role": "user", "content": content}]}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        e.close()
        raise RuntimeError(f"Spark API error {e.code}: {detail[:500]}")
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"unexpected Spark response shape: {json.dumps(data)[:500]}")


def load_procedures():
    data = load_json_file(PROCEDURES_PATH, [])
    return data if isinstance(data, list) else []


def execute_action(page, action):
    """Run one agent action dict against a Playwright page. Returns the kind."""
    kind = str(action.get("action") or "").lower()
    selector = action.get("selector") or ""
    value = action.get("value")
    if kind == "click":
        page.click(selector)
    elif kind == "fill":
        page.fill(selector, str(value or ""))
    elif kind == "select":
        page.select_option(selector, str(value or ""))
    elif kind == "check":
        page.check(selector)
    elif kind == "press":
        page.press(selector, str(value or "Enter"))
    elif kind == "goto":
        page.goto(str(value or selector))
    elif kind == "wait":
        page.wait_for_timeout(int(value) if isinstance(value, int) else 1000)
    elif kind in ("done", "clarify"):
        pass
    else:
        raise ValueError(f"unknown action: {kind!r}")
    return kind


def _append_history(record):
    history = load_json_file(REVISION_HISTORY_PATH, [])
    if not isinstance(history, list):
        history = []
    history.append(record)
    save_json_file(REVISION_HISTORY_PATH, history)


def _verify_turn(goal, action, screenshot_bytes):
    prompt = (
        f"Goal: {goal}\n"
        f"Action just taken: {json.dumps(action)}\n"
        f"Expected: {action.get('expected')}\n"
        "The screenshot shows the page after the action. "
        "Was the expected outcome achieved? Reply with ONLY JSON: "
        '{"achieved": true/false, "reason": "..."}'
    )
    try:
        raw = strip_code_fences(call_spark(
            prompt, base64.b64encode(screenshot_bytes).decode("ascii") if screenshot_bytes else None))
        verdict = json.loads(raw)
        return bool(verdict.get("achieved")), str(verdict.get("reason", ""))
    except (RuntimeError, ValueError):
        return False, "verification call failed"


def agent_loop(goal, procedure, page):
    """Screenshot -> decide ONE action -> act -> verify, up to MAX_TURNS.

    Every turn is appended to revision_history.json. A "clarify" action is
    appended to the supervisor queue and stops the loop. Returns the revision.
    """
    proc_id = procedure.get("id") if isinstance(procedure, dict) else None
    turns = []
    status = "incomplete"
    for turn in range(1, MAX_TURNS + 1):
        pre_path = f"/tmp/agent_turn_{turn}_pre.png"
        post_path = f"/tmp/agent_turn_{turn}_post.png"
        try:
            shot = page.screenshot()
        except Exception as e:  # noqa: BLE001 - browser failure ends the run
            status = "error"
            turns.append({"goal": goal, "turn": turn, "error": f"screenshot failed: {e}"})
            break
        with open(pre_path, "wb") as f:
            f.write(shot)
        decide_prompt = (
            "You are operating the Show Me Once receiving app in a browser.\n"
            f"Goal: {goal}\n"
            f"Procedure: {json.dumps(procedure)}\n"
            f"Turn {turn} of {MAX_TURNS}. Previous turns: {json.dumps(turns)}\n"
            "The screenshot shows the current page. Decide the ONE next browser action. "
            "Reply with ONLY this JSON object, no other text:\n"
            '{"action": "click|fill|select|check|press|goto|wait|done|clarify", '
            '"selector": "CSS selector (empty for goto/wait/done/clarify)", '
            '"value": "text, option, URL or ms (empty when unused)", '
            '"reasoning": "why this action", "expected": "what should change on the page"}\n'
            'Use "done" when the goal is fully achieved. Use "clarify" (question in '
            '"value") when blocked and a person must decide.'
        )
        try:
            action = json.loads(strip_code_fences(call_spark(
                decide_prompt, base64.b64encode(shot).decode("ascii"))))
        except (RuntimeError, ValueError):
            record = {"goal": goal, "turn": turn, "error": "decide step failed",
                      "screenshots": {"before": pre_path}, "decidedAt": now_iso()}
            turns.append(record)
            _append_history(record)
            continue
        if not isinstance(action, dict):
            record = {"goal": goal, "turn": turn, "error": "model did not return action JSON",
                      "screenshots": {"before": pre_path}, "decidedAt": now_iso()}
            turns.append(record)
            _append_history(record)
            continue
        kind = str(action.get("action") or "").lower()
        if kind == "clarify":
            question = action.get("value") or action.get("reasoning") or "agent needs input"
            queue = load_queue()
            queue.append({
                "reason": f"clarify: {question}",
                "detail": {"goal": goal, "procedure": proc_id, "turn": turn,
                           "question": question},
                "assignedTo": SUPERVISOR,
                "target": REVIEW_TARGET,
                "status": "pending",
                "createdAt": now_iso(),
            })
            save_json_file(QUEUE_PATH, queue)
            record = {"goal": goal, "turn": turn, "action": action, "status": "clarify",
                      "screenshots": {"before": pre_path}, "decidedAt": now_iso()}
            turns.append(record)
            _append_history(record)
            status = "clarify"
            break
        error = None
        try:
            execute_action(page, action)
        except Exception as e:  # noqa: BLE001 - bad selector etc; model can retry
            error = str(e)
        try:
            post = page.screenshot()
        except Exception as e:  # noqa: BLE001
            post = b""
            error = (error + "; " if error else "") + f"verify screenshot failed: {e}"
        if post:
            with open(post_path, "wb") as f:
                f.write(post)
        verified, reason = _verify_turn(goal, action, post)
        record = {"goal": goal, "turn": turn, "action": action,
                  "expected": action.get("expected"), "verified": verified,
                  "verifyReason": reason, "error": error,
                  "screenshots": {"before": pre_path, "after": post_path if post else None},
                  "decidedAt": now_iso()}
        turns.append(record)
        _append_history(record)
        if kind == "done":
            status = "done" if verified else "done_unverified"
            break
    return {"goal": goal, "procedure": proc_id, "status": status, "turns": turns}


INDEX_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Show Me Once</title></head>
<body style="font-family:sans-serif;max-width:640px;margin:40px auto">
<h1>Show Me Once — synthetic receiving</h1>
<p>One workflow, two layouts, same API. Pick a layout:</p>
<ul><li><a href="/layout-a">Layout A</a></li><li><a href="/layout-b">Layout B (changed layout)</a></li></ul>
<p><a href="/api/records">Saved records (JSON)</a> ·
<a href="/api/supervisor-queue">Supervisor queue (JSON)</a></p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "ShowMeOnce/0.2"

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, code, obj):
        self._send(code, json.dumps(obj, indent=2))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if len(path) > 1:
            path = path.rstrip("/")
        if path == "/":
            self._send(200, INDEX_HTML, "text/html")
        elif path in ("/layout-a", "/layout-b"):
            name = "layout_a.html" if path == "/layout-a" else "layout_b.html"
            try:
                with open(os.path.join(TEMPLATES, name), encoding="utf-8") as f:
                    self._send(200, f.read(), "text/html")
            except FileNotFoundError:
                self._send_json(404, {"error": "template missing"})
        elif path == "/api/purchase-orders":
            self._send_json(200, load_json_file(PO_PATH, []))
        elif path == "/api/deliveries":
            self._send_json(200, load_json_file(DELIVERIES_PATH, []))
        elif path == "/api/records":
            records = load_records()
            self._send_json(200, [e["current"] for e in records.values() if "current" in e])
        elif path == "/api/history":
            qs = urllib.parse.parse_qs(parsed.query)
            entry = load_records().get(qs.get("deliveryId", [""])[0])
            if not entry:
                self._send_json(404, {"error": "no records for that deliveryId"})
            else:
                self._send_json(200, entry)
        elif path == "/api/supervisor-queue":
            self._send_json(200, load_queue())
        elif path == "/procedures.json":
            self._send_json(200, load_procedures())
        elif path == "/revision_history.json":
            self._send_json(200, load_json_file(REVISION_HISTORY_PATH, []))
        elif path == "/supervisor_queue.json":
            self._send_json(200, load_queue())
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if len(path) > 1:
            path = path.rstrip("/")
        if path == "/api/learn-from-recording":
            self._handle_learn_from_recording()
            return
        if path == "/api/execute-procedure":
            self._handle_execute_procedure()
            return
        if path != "/api/reconcile":
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"error": "invalid JSON"})
            return

        if body.get("simulateFailure"):
            self._send_json(500, {"error": "simulated save failure; nothing persisted"})
            return

        delivery_id = body.get("deliveryId")
        deliveries = load_json_file(DELIVERIES_PATH, [])
        purchase_orders = load_json_file(PO_PATH, [])
        delivery = next((d for d in deliveries if d.get("id") == delivery_id), None)
        if delivery is None:
            self._send_json(400, {"error": f"unknown deliveryId: {delivery_id}"})
            return
        po = next((p for p in purchase_orders if p.get("id") == delivery.get("purchaseOrder")), None)
        if po is None:
            self._send_json(400, {"error": "delivery has no matching purchase order"})
            return
        ordered = po.get("orderedQuantity")
        if not isinstance(ordered, int) or isinstance(ordered, bool) or ordered <= 0:
            self._send_json(500, {"error": "purchase order has no positive integer orderedQuantity"})
            return

        note = body.get("note", "")
        if not isinstance(note, str):
            self._send_json(400, {"error": "note must be text"})
            return
        if len(note) > NOTE_MAX:
            self._send_json(400, {"error": f"note exceeds {NOTE_MAX} characters"})
            return

        try:
            received = parse_received(body.get("receivedQuantity", delivery.get("receivedQuantity")))
        except ValidationError as e:
            self._send_json(400, {"error": str(e)})
            return
        if received == 0 and not body.get("confirmZero"):
            self._send_json(400, {"error": "receivedQuantity is 0: set confirmZero to confirm nothing arrived, or supply the actual count"})
            return

        status, discrepancy, resolved = classify(ordered, received)
        records = load_records()
        entry = records.get(delivery_id, {})
        existing = entry.get("current")

        # M5: identical retry creates no new record.
        if existing and (existing.get("receivedQuantity"), existing.get("note"), existing.get("status")) == (received, note, status):
            self._send_json(200, {**existing, "deduped": True})
            return

        history = entry.get("history", [])
        if existing:
            reason = body.get("correctionReason", "")
            if not isinstance(reason, str) or not reason.strip():
                self._send_json(400, {"error": "changed reconciliation requires a human-confirmed correctionReason"})
                return
            history = history + [existing]
            revision = existing.get("revision", 1) + 1
            previous = {k: existing.get(k) for k in ("receivedQuantity", "status", "note", "reconciledAt")}
        else:
            reason, revision, previous = "", 1, None

        # Note is stored verbatim and never interpreted: notes and document
        # text are untrusted data, never instructions (O4/M6).
        record = {
            "deliveryId": delivery["id"],
            "revision": revision,
            "purchaseOrder": po["id"],
            "item": po.get("item"),
            "orderedQuantity": ordered,
            "receivedQuantity": received,
            "status": status,
            "discrepancy": discrepancy,
            "resolved": resolved,
            "note": note,
            "correctionReason": reason.strip() if existing else "",
            "previousValues": previous,
            "actor": body.get("actor", "operator"),
            "layout": body.get("layout", "unknown"),
            "reconciledAt": now_iso(),
        }
        records[delivery_id] = {"current": record, "history": history}
        save_json_file(RECORDS_PATH, records)
        self._update_queue(delivery_id, revision, status, discrepancy, resolved)
        self._send_json(200, record)

    def _update_queue(self, delivery_id, revision, status, discrepancy, resolved):
        """Simulated receiving-supervisor queue (M2/M3)."""
        queue = load_queue()
        if resolved:
            for item in queue:
                if item.get("deliveryId") == delivery_id and item.get("status") == "pending":
                    item["status"] = "resolved"
                    item["resolvedAt"] = now_iso()
                    item["resolvedByRevision"] = revision
        elif not any(i.get("deliveryId") == delivery_id and i.get("reason") == status and i.get("status") == "pending" for i in queue):
            queue.append({
                "deliveryId": delivery_id,
                "revision": revision,
                "reason": status,
                "detail": discrepancy,
                "assignedTo": SUPERVISOR,
                "target": REVIEW_TARGET,
                "status": "pending",
                "createdAt": now_iso(),
            })
        save_json_file(QUEUE_PATH, queue)

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return None

    def _handle_learn_from_recording(self):
        body = self._read_json_body()
        if body is None or not isinstance(body, dict):
            self._send_json(400, {"error": "invalid JSON"})
            return
        frames = body.get("frames", [])
        transcript = body.get("transcript", "")
        if (not isinstance(frames, list) or not frames) and not transcript:
            self._send_json(400, {"error": "need frames (base64 array) and/or a transcript"})
            return
        first = frames[0] if isinstance(frames, list) and frames else None
        prompt = (
            "Extract a reusable browser procedure from this screen recording of the "
            "Show Me Once receiving app.\n"
            f"Transcript:\n{transcript}\n"
            f"({len(frames) if isinstance(frames, list) else 0} frames captured; "
            "the first frame image is attached.)\n"
            "App controls: delivery dropdown (#delivery), purchase-order text (#po), "
            "received quantity (#qty), note (#note), correction reason (#reason), "
            "Reconcile button (#go), confirm-zero checkbox (#zero).\n"
            "Reply with ONLY this JSON object, no other text:\n"
            '{"id": "short-kebab-id", "name": "...", "goal": "...", '
            '"steps": [{"order": 1, "instruction": "...", "selector": "..."}], '
            '"rules": [{"id": "...", "rule": "..."}], '
            '"selectors": {"delivery": "#delivery", "qty": "#qty", "note": "#note", '
            '"reason": "#reason", "go": "#go", "zero": "#zero"}}'
        )
        try:
            entry = json.loads(strip_code_fences(call_spark(prompt, first)))
        except RuntimeError as e:
            self._send_json(500, {"error": str(e)})
            return
        except ValueError:
            self._send_json(500, {"error": "model did not return procedure JSON"})
            return
        if not isinstance(entry, dict):
            self._send_json(500, {"error": "model did not return procedure JSON"})
            return
        procedures = load_procedures()
        entry.setdefault("id", f"procedure-{len(procedures) + 1}")
        entry.setdefault("source", "learned from recording")
        procedures.append(entry)
        save_json_file(PROCEDURES_PATH, procedures)
        self._send_json(200, entry)

    def _handle_execute_procedure(self):
        body = self._read_json_body()
        if body is None or not isinstance(body, dict):
            self._send_json(400, {"error": "invalid JSON"})
            return
        po_number = body.get("po_number", body.get("poNumber", ""))
        procedures = load_procedures()
        proc = next((p for p in procedures if p.get("id") == po_number), None)
        if proc is None and body.get("procedure_id"):
            proc = next((p for p in procedures if p.get("id") == body["procedure_id"]), None)
        if proc is None:
            proc = procedures[0] if procedures else None
        if proc is None:
            self._send_json(404, {"error": "no procedures; POST /api/learn-from-recording first"})
            return
        goal = body.get("goal") or f"{proc.get('goal', 'Reconcile delivery')} (PO {po_number})"
        layout = str(body.get("layout", "layout-b")).lstrip("/") or "layout-b"
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._send_json(500, {"error": "playwright is not installed; run: pip install playwright && playwright install chromium"})
            return
        port = self.server.server_address[1]
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=False)
                page = browser.new_page()
                try:
                    page.goto(f"http://127.0.0.1:{port}/{layout}")
                    revision = agent_loop(goal, proc, page)
                finally:
                    browser.close()
        except Exception as e:  # noqa: BLE001 - surface browser/model failures as 500
            self._send_json(500, {"error": str(e)[:500]})
            return
        self._send_json(200, {"po_number": po_number, "procedure": proc.get("id"),
                              "revision": revision})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Show Me Once receiving app on http://127.0.0.1:{port} (layouts: /layout-a, /layout-b)")
    server.serve_forever()
