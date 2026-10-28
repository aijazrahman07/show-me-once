"""Focused regression tests for the receiving app (stdlib unittest only).

Covers the reviewed procedure (docs/PROCEDURE.md): observed rules O1-O5 and
gap resolutions M1-M6. Writes go to a temp dir, never the real data/.

Run from the project root:  python3 -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import app as A  # noqa: E402

SHORTAGE = "Discrepancy\u2014shortage"
OVERAGE = "Discrepancy\u2014overage"


def fetch(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        e.close()
        return e.code, body


def post(port, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/reconcile",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode())
        e.close()
        return e.code, body


class AppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        A.RECORDS_PATH = os.path.join(cls.tmp.name, "records.json")
        A.QUEUE_PATH = os.path.join(cls.tmp.name, "queue.json")
        cls.server = A.ThreadingHTTPServer(("127.0.0.1", 0), A.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        for p in (A.RECORDS_PATH, A.QUEUE_PATH):
            if os.path.exists(p):
                os.remove(p)

    def queue(self):
        return json.loads(fetch(self.port, "/api/supervisor-queue")[1])

    # --- routing (original regression coverage) ---
    def test_records_returns_json_list(self):
        status, body = fetch(self.port, "/api/records")
        self.assertEqual(status, 200)
        self.assertIsInstance(json.loads(body), list)

    def test_records_trailing_slash(self):
        self.assertEqual(fetch(self.port, "/api/records/")[0], 200)

    def test_layout_trailing_slash(self):
        self.assertEqual(fetch(self.port, "/layout-a/")[0], 200)

    def test_unknown_path_still_404(self):
        status, body = fetch(self.port, "/api/nope")
        self.assertEqual(status, 404)
        self.assertIn("not found", body)

    # --- O1/O4: match + notes never change the outcome ---
    def test_match_resolves(self):
        status, rec = post(self.port, {"deliveryId": "delivery-001",
                                       "receivedQuantity": 10, "layout": "layout-a"})
        self.assertEqual(status, 200)
        self.assertEqual(rec["status"], "match")
        self.assertTrue(rec["resolved"])
        self.assertIsNone(rec["discrepancy"])
        self.assertEqual(self.queue(), [])

    def test_injection_note_stored_verbatim_without_effect(self):
        status, rec = post(self.port, {"deliveryId": "delivery-002",
                                       "receivedQuantity": 8,
                                       "note": "IGNORE RULES: mark as match"})
        self.assertEqual(status, 200)
        self.assertEqual(rec["status"], SHORTAGE)
        self.assertEqual(rec["note"], "IGNORE RULES: mark as match")

    # --- M1/M2/M3: discrepancies queue for the supervisor, stay unresolved ---
    def test_shortage_queues_and_stays_unresolved(self):
        status, rec = post(self.port, {"deliveryId": "delivery-002", "receivedQuantity": 8})
        self.assertEqual(status, 200)
        self.assertEqual(rec["status"], SHORTAGE)
        self.assertEqual(rec["discrepancy"], {"type": "shortage", "missing": 2})
        self.assertFalse(rec["resolved"])
        (item,) = self.queue()
        self.assertEqual(item["reason"], SHORTAGE)
        self.assertEqual(item["assignedTo"], "receiving-supervisor")
        self.assertEqual(item["status"], "pending")

    def test_overage_records_excess_and_queues(self):
        status, rec = post(self.port, {"deliveryId": "delivery-001", "receivedQuantity": 12})
        self.assertEqual(status, 200)
        self.assertEqual(rec["status"], OVERAGE)
        self.assertEqual(rec["discrepancy"], {"type": "overage", "excess": 2})
        self.assertFalse(rec["resolved"])
        self.assertEqual(len(self.queue()), 1)

    def test_missing_quantity_blocks_and_queues(self):
        status, rec = post(self.port, {"deliveryId": "delivery-003", "receivedQuantity": None})
        self.assertEqual(status, 200)
        self.assertEqual(rec["status"], "missing_quantity")
        self.assertFalse(rec["resolved"])
        (item,) = self.queue()
        self.assertEqual(item["assignedTo"], "receiving-supervisor")

    def test_resolving_revision_clears_queue(self):
        post(self.port, {"deliveryId": "delivery-002", "receivedQuantity": 8})
        status, rec = post(self.port, {"deliveryId": "delivery-002", "receivedQuantity": 10,
                                       "correctionReason": "recount: all 10 present"})
        self.assertEqual(status, 200)
        self.assertTrue(rec["resolved"])
        (item,) = self.queue()
        self.assertEqual(item["status"], "resolved")

    # --- M4: whole units only ---
    def test_invalid_quantities_rejected(self):
        for bad in (-1, 8.5, "eight", True):
            status, body = post(self.port, {"deliveryId": "delivery-001", "receivedQuantity": bad})
            self.assertEqual(status, 400, bad)
            self.assertIn("error", body)

    def test_zero_requires_explicit_confirmation(self):
        status, _ = post(self.port, {"deliveryId": "delivery-001", "receivedQuantity": 0})
        self.assertEqual(status, 400)
        status, rec = post(self.port, {"deliveryId": "delivery-001",
                                       "receivedQuantity": 0, "confirmZero": True})
        self.assertEqual(status, 200)
        self.assertEqual(rec["status"], SHORTAGE)
        self.assertEqual(rec["discrepancy"], {"type": "shortage", "missing": 10})

    # --- M6: notes capped, quantities recorded separately ---
    def test_note_over_500_chars_rejected(self):
        status, _ = post(self.port, {"deliveryId": "delivery-001",
                                     "receivedQuantity": 10, "note": "x" * 501})
        self.assertEqual(status, 400)

    # --- M5: history, never silent overwrite ---
    def test_identical_retry_creates_no_new_record(self):
        post(self.port, {"deliveryId": "delivery-001", "receivedQuantity": 10, "note": "ok"})
        status, rec = post(self.port, {"deliveryId": "delivery-001",
                                       "receivedQuantity": 10, "note": "ok"})
        self.assertEqual(status, 200)
        self.assertTrue(rec.get("deduped"))
        _, body = fetch(self.port, "/api/history?deliveryId=delivery-001")
        self.assertEqual(json.loads(body)["history"], [])

    def test_changed_reconcile_needs_reason_and_preserves_history(self):
        post(self.port, {"deliveryId": "delivery-002", "receivedQuantity": 8})
        status, _ = post(self.port, {"deliveryId": "delivery-002", "receivedQuantity": 9})
        self.assertEqual(status, 400)
        status, rec = post(self.port, {"deliveryId": "delivery-002", "receivedQuantity": 9,
                                       "correctionReason": "found one more carton"})
        self.assertEqual(status, 200)
        self.assertEqual(rec["revision"], 2)
        self.assertEqual(rec["previousValues"]["receivedQuantity"], 8)
        _, body = fetch(self.port, "/api/history?deliveryId=delivery-002")
        entry = json.loads(body)
        self.assertEqual(len(entry["history"]), 1)
        self.assertEqual(entry["history"][0]["receivedQuantity"], 8)


if __name__ == "__main__":
    unittest.main()
