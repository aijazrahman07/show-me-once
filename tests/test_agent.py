"""Tests for the Spark agent extension (no network, no real browser).

Run from the project root:  python3 -m unittest discover -s tests
"""
import base64
import glob
import importlib.util
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import app as A  # noqa: E402


def post_json(port, path, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
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


def post_raw(port, path, raw):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=raw.encode(),
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


def fetch(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return r.status, json.loads(r.read().decode())


class FakePage:
    def __init__(self):
        self.calls = []

    def screenshot(self):
        return b"fake-png-bytes"

    def click(self, selector):
        self.calls.append(("click", selector))

    def fill(self, selector, value):
        self.calls.append(("fill", selector, value))

    def select_option(self, selector, value):
        self.calls.append(("select", selector, value))

    def check(self, selector):
        self.calls.append(("check", selector))

    def press(self, selector, key):
        self.calls.append(("press", selector, key))

    def goto(self, url):
        self.calls.append(("goto", url))

    def wait_for_timeout(self, ms):
        self.calls.append(("wait", ms))


def cleanup_screenshots():
    seen = set()
    for p in (glob.glob("/tmp/agent_turn_*_pre.png") + glob.glob("/tmp/agent_turn_*_post.png")
              + glob.glob("/tmp/agent_turn_*_crop.png")
              + glob.glob("/tmp/agent_turn_*_verify_crop.png")):
        if p not in seen:
            seen.add(p)
            os.remove(p)


class SparkUnitTest(unittest.TestCase):
    def test_strip_plain(self):
        self.assertEqual(A.strip_code_fences('{"a": 1}'), '{"a": 1}')

    def test_strip_json_fence(self):
        self.assertEqual(A.strip_code_fences('```json\n{"a": 1}\n```'), '{"a": 1}')

    def test_strip_bare_fence(self):
        self.assertEqual(A.strip_code_fences('```\n{"a": 1}\n```'), '{"a": 1}')

    def _fake_transport(self, seen):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode()

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            seen["payload"] = json.loads(req.data.decode())
            seen["auth"] = req.get_header("Authorization")
            return Resp()

        return fake_urlopen

    def test_call_spark_text(self):
        seen = {}
        with mock.patch.dict(os.environ, {"META_API_KEY": "k123"}), \
                mock.patch.object(A.urllib.request, "urlopen", self._fake_transport(seen)):
            self.assertEqual(A.call_spark("hello"), "hi")
        self.assertEqual(seen["url"], "https://api.meta.ai/v1/chat/completions")
        self.assertEqual(seen["payload"]["model"], "muse-spark-1.3")
        self.assertEqual(seen["payload"]["messages"][0]["content"], "hello")
        self.assertEqual(seen["auth"], "Bearer k123")

    def test_call_spark_image(self):
        seen = {}
        with mock.patch.dict(os.environ, {"META_API_KEY": "k123"}), \
                mock.patch.object(A.urllib.request, "urlopen", self._fake_transport(seen)):
            A.call_spark("see this", "aW1hZ2U=")
        content = seen["payload"]["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "see this"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].endswith("aW1hZ2U="))

    def test_call_spark_no_key(self):
        with mock.patch.object(A, "get_meta_api_key", return_value=""):
            with self.assertRaises(RuntimeError):
                A.call_spark("hello")

    def test_execute_action_mapping(self):
        page = FakePage()
        self.assertEqual(A.execute_action(page, {"action": "click", "selector": "#go"}), "click")
        self.assertEqual(A.execute_action(page, {"action": "fill", "selector": "#qty", "value": 8}), "fill")
        self.assertEqual(A.execute_action(page, {"action": "select", "selector": "#d", "value": "x"}), "select")
        self.assertEqual(A.execute_action(page, {"action": "check", "selector": "#z"}), "check")
        self.assertEqual(A.execute_action(page, {"action": "press", "selector": "#q", "value": "Enter"}), "press")
        self.assertEqual(A.execute_action(page, {"action": "goto", "value": "http://x/"}), "goto")
        self.assertEqual(A.execute_action(page, {"action": "wait", "value": 50}), "wait")
        self.assertEqual(A.execute_action(page, {"action": "done"}), "done")
        self.assertEqual(A.execute_action(page, {"action": "clarify"}), "clarify")
        with self.assertRaises(ValueError):
            A.execute_action(page, {"action": "dance"})
        self.assertIn(("click", "#go"), page.calls)
        self.assertIn(("fill", "#qty", "8"), page.calls)


class AgentLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(cleanup_screenshots)
        self.orig_q, self.orig_h = A.QUEUE_PATH, A.REVISION_HISTORY_PATH
        A.QUEUE_PATH = os.path.join(self.tmp.name, "q.json")
        A.REVISION_HISTORY_PATH = os.path.join(self.tmp.name, "h.json")
        self.addCleanup(self._restore)

    def _restore(self):
        A.QUEUE_PATH, A.REVISION_HISTORY_PATH = self.orig_q, self.orig_h

    def test_clarify_breaks_and_queues(self):
        page = FakePage()
        fake = lambda prompt, image_b64=None: json.dumps(  # noqa: E731
            {"action": "clarify", "selector": "", "value": "which delivery?",
             "reasoning": "ambiguous", "expected": ""})
        with mock.patch.object(A, "call_spark", new=fake):
            rev = A.agent_loop("reconcile PO-001", {"id": "reconcile-delivery"}, page)
        self.assertEqual(rev["status"], "clarify")
        self.assertEqual(len(rev["turns"]), 1)
        self.assertEqual(page.calls, [])
        with open(A.QUEUE_PATH, encoding="utf-8") as f:
            (item,) = json.load(f)
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["assignedTo"], "receiving-supervisor")
        self.assertIn("which delivery?", item["reason"])
        with open(A.REVISION_HISTORY_PATH, encoding="utf-8") as f:
            history = json.load(f)
        self.assertEqual(len(history), 1)
        self.assertTrue(os.path.exists("/tmp/agent_turn_1_pre.png"))

    def test_act_verify_done(self):
        script = [
            json.dumps({"action": "click", "selector": "#go", "value": "",
                        "reasoning": "submit", "expected": "status shown"}),
            json.dumps({"achieved": True, "reason": "status shown"}),
            json.dumps({"action": "done", "selector": "", "value": "",
                        "reasoning": "goal met", "expected": "nothing further"}),
            json.dumps({"achieved": True, "reason": "goal met"}),
        ]

        def fake(prompt, image_b64=None):
            self.assertTrue(image_b64, "screenshots must feed the model")
            return script.pop(0)

        with mock.patch.object(A, "call_spark", new=fake):
            rev = A.agent_loop("reconcile PO-001", {"id": "reconcile-delivery"}, FakePage())
        self.assertEqual(rev["status"], "done")
        self.assertEqual(len(rev["turns"]), 2)
        self.assertTrue(all(t["verified"] for t in rev["turns"]))
        with open(A.REVISION_HISTORY_PATH, encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), 2)


class AgentServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.orig = (A.PROCEDURES_PATH, A.REVISION_HISTORY_PATH, A.QUEUE_PATH)
        A.PROCEDURES_PATH = os.path.join(cls.tmp.name, "procedures.json")
        A.REVISION_HISTORY_PATH = os.path.join(cls.tmp.name, "history.json")
        A.QUEUE_PATH = os.path.join(cls.tmp.name, "queue.json")
        cls.server = A.ThreadingHTTPServer(("127.0.0.1", 0), A.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        A.PROCEDURES_PATH, A.REVISION_HISTORY_PATH, A.QUEUE_PATH = cls.orig
        cls.tmp.cleanup()

    def setUp(self):
        for p in (A.PROCEDURES_PATH, A.REVISION_HISTORY_PATH, A.QUEUE_PATH):
            if os.path.exists(p):
                os.remove(p)

    def test_static_json_routes(self):
        with open(A.PROCEDURES_PATH, "w", encoding="utf-8") as f:
            json.dump([{"id": "p1"}], f)
        self.assertEqual(fetch(self.port, "/procedures.json"), (200, [{"id": "p1"}]))
        self.assertEqual(fetch(self.port, "/revision_history.json"), (200, []))
        self.assertEqual(fetch(self.port, "/supervisor_queue.json"), (200, []))

    def test_learn_from_recording(self):
        entry = {"id": "learned-1", "name": "Learned", "goal": "do it", "steps": []}
        fake = lambda prompt, image_b64=None: "```json\n" + json.dumps(entry) + "\n```"  # noqa: E731
        with mock.patch.object(A, "call_spark", new=fake):
            status, body = post_json(self.port, "/api/learn-from-recording",
                                     {"frames": ["ZmFrbWU="], "transcript": "click reconcile"})
        self.assertEqual(status, 200)
        self.assertEqual(body["id"], "learned-1")
        with open(A.PROCEDURES_PATH, encoding="utf-8") as f:
            self.assertEqual(json.load(f)[0]["id"], "learned-1")

    def test_learn_from_recording_rejects_empty(self):
        self.assertEqual(post_json(self.port, "/api/learn-from-recording", {})[0], 400)
        self.assertEqual(post_raw(self.port, "/api/learn-from-recording", "nope")[0], 400)

    def test_execute_procedure_no_procedures(self):
        self.assertEqual(post_json(self.port, "/api/execute-procedure", {"po_number": "PO-1"})[0], 404)
        self.assertEqual(post_raw(self.port, "/api/execute-procedure", "nope")[0], 400)

    def test_execute_procedure_runs_loop(self):
        with open(A.PROCEDURES_PATH, "w", encoding="utf-8") as f:
            json.dump([{"id": "reconcile-delivery", "goal": "reconcile it"}], f)
        made = {}

        class FakeBrowser:
            def new_page(self):
                made["page"] = FakePage()
                return made["page"]

            def close(self):
                made["closed"] = True

        class FakeChromium:
            def launch(self, headless=False):
                made["headless"] = headless
                return FakeBrowser()

        class FakePW:
            chromium = FakeChromium()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        sync_mod = types.ModuleType("playwright.sync_api")
        sync_mod.sync_playwright = lambda: FakePW()
        pw_mod = types.ModuleType("playwright")
        with mock.patch.dict(sys.modules, {"playwright": pw_mod, "playwright.sync_api": sync_mod}), \
                mock.patch.object(A, "agent_loop",
                                  return_value={"status": "done", "turns": []}) as mloop:
            status, body = post_json(self.port, "/api/execute-procedure", {"po_number": "PO-001"})
        self.assertEqual(status, 200)
        self.assertEqual(body["po_number"], "PO-001")
        self.assertEqual(body["procedure"], "reconcile-delivery")
        self.assertEqual(body["revision"]["status"], "done")
        self.assertFalse(made["headless"])
        self.assertTrue(made["closed"])
        goal = mloop.call_args[0][0]
        self.assertIn("PO-001", goal)
        self.assertEqual(made["page"].calls[0], ("goto", f"http://127.0.0.1:{self.port}/layout-b"))


class SamTest(unittest.TestCase):
    def test_no_key_returns_none(self):
        with mock.patch.dict(os.environ, {"SAM_API_KEY": ""}), \
                mock.patch.object(A, "get_meta_api_key", return_value=""):
            self.assertIsNone(A.call_sam_31("aGk=", "button"))

    def test_missing_requests_returns_none(self):
        with mock.patch.dict(os.environ, {"SAM_API_KEY": "k"}), \
                mock.patch.dict(sys.modules, {"requests": None}):
            self.assertIsNone(A.call_sam_31("aGk=", "button"))

    def test_parses_best_box(self):
        class Box:
            def __init__(self, left, top, right, bottom, confidence):
                self.left, self.top, self.right, self.bottom = left, top, right, bottom
                self.confidence = confidence

        class Snap:
            def __init__(self, records):
                self.records = records

        class Parser:
            def push(self, text):
                assert "<0f>" in text
                return [Snap([Box(10, 20, 110, 120, 0.4), Box(0, 0, 5, 5, 0.9),
                               "not-a-box"])]

        class Fmt:
            def create_parser(self):
                return Parser()

        fake_msp = types.ModuleType("meta_sam_parser")
        fake_msp.SegmentationBoxRecord = Box
        fake_msp.image_segmentation_format = lambda: Fmt()
        seen = {}

        class Resp:
            status_code = 200

            def json(self):
                return {"output": [{"content": [{"text": "<0f>0<|box|>"}]}]}

        fake_requests = types.ModuleType("requests")

        def post(url, headers=None, json=None, timeout=None):
            seen["url"] = url
            seen["body"] = json
            return Resp()

        fake_requests.post = post
        with mock.patch.dict(os.environ, {"SAM_API_KEY": "k"}), \
                mock.patch.dict(sys.modules, {"requests": fake_requests,
                                              "meta_sam_parser": fake_msp}):
            out = A.call_sam_31("aW1hZ2U=", "button")
        self.assertEqual(out, {"bbox": [0, 0, 5, 5], "score": 0.9, "boxes": 2})
        self.assertEqual(seen["url"], A.SAM_RESPONSES_URL)
        self.assertEqual(seen["body"]["model"], "sam-3.1")
        self.assertFalse(seen["body"]["stream"])

    def test_transport_failure_returns_none(self):
        fake_requests = types.ModuleType("requests")

        def post(url, headers=None, json=None, timeout=None):
            raise ConnectionError("down")

        fake_requests.post = post
        with mock.patch.dict(os.environ, {"SAM_API_KEY": "k"}), \
                mock.patch.dict(sys.modules, {"requests": fake_requests}):
            self.assertIsNone(A.call_sam_31("aGk=", "button"))

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "PIL only in venv")
    def test_crop_math(self):
        import io

        from PIL import Image
        img = Image.new("RGB", (100, 100), "white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        full = base64.b64encode(buf.getvalue()).decode()
        out = A.crop_with_sam(full, {"bbox": [10, 10, 50, 50]}, pad_px=0)
        self.assertNotEqual(out, full)
        self.assertEqual(Image.open(io.BytesIO(base64.b64decode(out))).size, (50, 50))

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "PIL only in venv")
    def test_crop_rejects_tiny_box(self):
        import io

        from PIL import Image
        img = Image.new("RGB", (100, 100), "white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        full = base64.b64encode(buf.getvalue()).decode()
        self.assertEqual(A.crop_with_sam(full, {"bbox": [0, 0, 5, 5]}), full)

    def test_crop_without_pil_returns_original(self):
        with mock.patch.dict(sys.modules, {"PIL": None}):
            self.assertEqual(A.crop_with_sam("aGk=", {"bbox": [0, 0, 10, 10]}), "aGk=")

    def test_crop_bad_input_returns_original(self):
        self.assertEqual(A.crop_with_sam("!!!not-b64!!!", {"bbox": [0, 0, 50, 50]}),
                         "!!!not-b64!!!")
        self.assertEqual(A.crop_with_sam("aGk=", None), "aGk=")

    def test_sam_disabled_without_imports(self):
        with mock.patch.dict(sys.modules, {"requests": None}):
            self.assertFalse(A.sam_enabled())


class SamLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(cleanup_screenshots)
        self.orig_q, self.orig_h = A.QUEUE_PATH, A.REVISION_HISTORY_PATH
        A.QUEUE_PATH = os.path.join(self.tmp.name, "q.json")
        A.REVISION_HISTORY_PATH = os.path.join(self.tmp.name, "h.json")
        self.addCleanup(self._restore)

    def _restore(self):
        A.QUEUE_PATH, A.REVISION_HISTORY_PATH = self.orig_q, self.orig_h

    def _done_script(self, seen):
        def fake(prompt, image_b64=None):
            seen.append(image_b64)
            if "ONE next browser action" in prompt:
                return json.dumps({"action": "done", "selector": "", "value": "",
                                   "reasoning": "r", "expected": "e"})
            return json.dumps({"achieved": True, "reason": "ok"})
        return fake

    def test_sam_crop_feeds_decide(self):
        seen = []
        crop_b64 = base64.b64encode(b"cropbytes").decode()
        decide_sam = {"bbox": [0, 0, 640, 450], "score": 0.5, "boxes": 3}
        with mock.patch.object(A, "sam_enabled", return_value=True), \
                mock.patch.object(A, "call_sam_31", side_effect=[decide_sam, None]), \
                mock.patch.object(A, "crop_with_sam", return_value=crop_b64), \
                mock.patch.object(A, "call_spark", new=self._done_script(seen)):
            rev = A.agent_loop("g", {"id": "x", "sam_prompt": "controls"}, FakePage())
        full = base64.b64encode(b"fake-png-bytes").decode()
        self.assertEqual(seen[0], crop_b64)
        self.assertEqual(seen[1], full)
        sam = rev["turns"][0]["sam"]
        self.assertTrue(sam["used"])
        self.assertEqual(sam["score"], 0.5)
        self.assertTrue(os.path.exists("/tmp/agent_turn_1_crop.png"))
        sam_verify = rev["turns"][0]["sam_verify"]
        self.assertFalse(sam_verify["used"])
        self.assertEqual(sam_verify["reason"], "no grounding")

    def test_sam_none_falls_back_to_full(self):
        seen = []
        with mock.patch.object(A, "sam_enabled", return_value=True), \
                mock.patch.object(A, "call_sam_31", return_value=None), \
                mock.patch.object(A, "call_spark", new=self._done_script(seen)):
            rev = A.agent_loop("g", {"id": "x", "sam_prompt": "controls"}, FakePage())
        full = base64.b64encode(b"fake-png-bytes").decode()
        self.assertEqual(seen[0], full)
        self.assertEqual(seen[1], full)
        sam = rev["turns"][0]["sam"]
        self.assertFalse(sam["used"])
        self.assertEqual(sam["reason"], "no grounding")
        sam_verify = rev["turns"][0]["sam_verify"]
        self.assertFalse(sam_verify["used"])

    def test_verify_uses_own_crop(self):
        seen = []
        verify_crop = base64.b64encode(b"verifycrop").decode()
        verify_sam = {"bbox": [10, 10, 400, 300], "score": 0.7, "boxes": 5}
        with mock.patch.object(A, "sam_enabled", return_value=True), \
                mock.patch.object(A, "call_sam_31", side_effect=[verify_sam]), \
                mock.patch.object(A, "crop_with_sam", return_value=verify_crop), \
                mock.patch.object(A, "call_spark", new=self._done_script(seen)):
            rev = A.agent_loop("g", {"id": "x", "sam_verify_prompt": "delivery table"},
                               FakePage())
        full = base64.b64encode(b"fake-png-bytes").decode()
        self.assertEqual(seen[0], full)
        self.assertEqual(seen[1], verify_crop)
        sam_verify = rev["turns"][0]["sam_verify"]
        self.assertTrue(sam_verify["used"])
        self.assertEqual(sam_verify["score"], 0.7)
        self.assertEqual(sam_verify["prompt"], "delivery table")
        self.assertTrue(os.path.exists("/tmp/agent_turn_1_verify_crop.png"))

    def test_no_sam_prompt_skips_sam(self):
        seen = []
        with mock.patch.object(A, "call_sam_31") as msam, \
                mock.patch.object(A, "call_spark", new=self._done_script(seen)):
            A.agent_loop("g", {"id": "x"}, FakePage())
        msam.assert_not_called()


if __name__ == "__main__":
    unittest.main()
