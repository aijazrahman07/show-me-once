#!/usr/bin/env python3
"""Pre-flight checks before recording the demo video.

Run from the project root (or anywhere; paths resolve from this file):

    .venv/bin/python scripts/preflight.py

Live checks (start the server first with `.venv/bin/python src/app.py`):

    CHECK_LIVE=1 .venv/bin/python scripts/preflight.py

The API key is read the same way as the app: META_API_KEY env var first,
then .env (MODEL_API_KEY accepted). Nothing prints the key.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import app  # noqa: E402  (reuse the app's own key lookup)

results = []


def check(name, fn):
    try:
        fn()
        print(f"PASS  {name}")
        results.append(True)
    except Exception as e:  # noqa: BLE001 - report any check failure
        print(f"FAIL  {name}: {e}")
        results.append(False)


def has_key():
    assert app.get_meta_api_key(), "no key (checked META_API_KEY/MODEL_API_KEY env + .env)"


def has_requirements():
    import playwright  # noqa: F401
    import requests  # noqa: F401
    import PIL  # noqa: F401
    import meta_sam_parser  # noqa: F401


def has_chromium():
    """Full Chromium build (headed-capable), macOS or Linux cache location."""
    roots = (os.path.expanduser("~/.cache/ms-playwright"),
             os.path.expanduser("~/Library/Caches/ms-playwright"))
    for r in roots:
        if os.path.isdir(r) and any(
                d.startswith("chromium-") and "headless" not in d for d in os.listdir(r)):
            return
    raise AssertionError("no full chromium build in ms-playwright cache")


def has_app():
    assert os.path.isfile(os.path.join(ROOT, "src", "app.py")), "src/app.py missing"


def data_file(name, default):
    path = os.path.join(ROOT, "data", name)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def has_procedures():
    procs = data_file("procedures.json", [])
    assert isinstance(procs, list) and procs, "procedures.json is empty"


def tests_pass():
    r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stderr.strip().splitlines()[-1] if r.stderr else "unittest failed"


check("API key set (env or .env)", has_key)
check("requirements installed (playwright, requests, PIL, meta-sam-parser)", has_requirements)
check("Playwright Chromium installed (headed-capable)", has_chromium)
check("src/app.py exists", has_app)
check("data/procedures.json exists, non-empty & valid JSON", has_procedures)
for f in ("supervisor_queue.json", "revision_history.json"):
    check(f"data/{f} exists & valid JSON", lambda p=f: data_file(p, []))
check("tests pass (unittest)", tests_pass)

if os.getenv("CHECK_LIVE"):
    import requests

    def server_up():
        r = requests.get("http://127.0.0.1:8000", timeout=5)
        assert r.status_code == 200, f"HTTP {r.status_code}"

    def meta_reachable():
        r = requests.post(
            "https://api.meta.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {app.get_meta_api_key()}"},
            json={"model": "muse-spark-1.3",
                  "messages": [{"role": "user", "content": "ping"}]},
            timeout=30)
        assert r.status_code in (200, 400), f"HTTP {r.status_code}: {r.text[:200]}"

    check("Server at 127.0.0.1:8000", server_up)
    check("Meta API reachable", meta_reachable)

print("\n" + "=" * 40)
if all(results):
    print("ALL CHECKS PASSED — ready to record video")
    sys.exit(0)
print(f"FAILED {results.count(False)}/{len(results)} — fix above before recording")
sys.exit(1)
