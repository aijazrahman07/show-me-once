"""Synthetic purchase-order / delivery reconciliation app.

Stdlib only. Serves two controlled layouts over one API and implements the
reviewed procedure in docs/PROCEDURE.md (observed rules O1-O5, gap
resolutions M1-M6).

Run:  python3 src/app.py [port]   (default 8000)
"""
import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "data", "fixtures")
TEMPLATES = os.path.join(ROOT, "src", "templates")
RECORDS_PATH = os.path.join(ROOT, "data", "records.json")
QUEUE_PATH = os.path.join(ROOT, "data", "supervisor_queue.json")
PO_PATH = os.path.join(FIXTURES, "purchase_orders.json")
DELIVERIES_PATH = os.path.join(FIXTURES, "deliveries.json")

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
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if len(path) > 1:
            path = path.rstrip("/")
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

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    import sys

    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Show Me Once receiving app on http://127.0.0.1:{port} (layouts: /layout-a, /layout-b)")
    server.serve_forever()
