"""Milestone 5: execute the reviewed procedure on unseen deliveries.

Demonstration-assisted executor. Follows docs/PROCEDURE.md (reviewed
2026-10-28) deterministically — no model calls. Runs every held-out case on
the changed layout (layout-b) and persists the run to
data/eval_runs/<timestamp>/ for independent verification (milestone 6).

Cases the procedure leaves to a human (M4 rejects) are surfaced, not
self-fixed: each counts one intervention and must create no record.

Run from the project root:  python3 scripts/run_heldout.py
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import app as A  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAYOUT = "layout-b"
ACTOR = "heldout-executor"


def api(port, method, path, payload=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode())
        e.close()
        return e.code, body


def run_case(port, case):
    did = case["delivery"]["id"]
    exp = case["expected"]
    t0 = time.perf_counter()
    status, body = api(port, "POST", "/api/reconcile",
                       {"deliveryId": did, **case["input"],
                        "layout": LAYOUT, "actor": ACTOR})
    ms = round((time.perf_counter() - t0) * 1000, 1)
    result = {"id": case["id"], "http": status, "ms": ms, "intervention": False}

    if exp.get("http") == 400:
        # Agent must surface to a human, never self-fix or guess.
        _, history = api(port, "GET", f"/api/history?deliveryId={did}")
        result["intervention"] = True
        no_record = isinstance(history, dict) and "current" not in history
        result["pass"] = status == 400 and no_record
        result["detail"] = body.get("error", "")
        result["expectedReason"] = exp.get("reason", "")
        return result

    checks = {
        "http200": status == 200,
        "status": body.get("status") == exp["status"],
        "resolved": body.get("resolved") is exp["resolved"],
    }
    _, queue = api(port, "GET", "/api/supervisor-queue")
    pending = [q for q in queue if q.get("deliveryId") == did and q.get("status") == "pending"]
    checks["queued"] = bool(pending) is exp["queued"]
    if "missing" in exp:
        checks["missing"] = (body.get("discrepancy") or {}).get("missing") == exp["missing"]
    if "excess" in exp:
        checks["excess"] = (body.get("discrepancy") or {}).get("excess") == exp["excess"]
    if exp.get("noteVerbatim"):
        checks["noteVerbatim"] = body.get("note") == case["input"]["note"]

    if "correct" in case:
        cstatus, crec = api(port, "POST", "/api/reconcile",
                            {"deliveryId": did, **case["correct"],
                             "layout": LAYOUT, "actor": ACTOR})
        want = case["expectedAfterCorrect"]
        _, queue = api(port, "GET", "/api/supervisor-queue")
        mine = [q for q in queue if q.get("deliveryId") == did]
        checks["correctHttp"] = cstatus == 200
        checks["correctStatus"] = crec.get("status") == want["status"]
        checks["correctResolved"] = crec.get("resolved") is want["resolved"]
        checks["revision"] = crec.get("revision") == want["revision"]
        checks["queueResolved"] = all(q.get("status") == "resolved" for q in mine) is want["queueResolved"]

    result["checks"] = checks
    result["pass"] = all(checks.values())
    result["status"] = body.get("status")
    return result


def main():
    with open(os.path.join(ROOT, "data", "fixtures", "eval_cases.json"), encoding="utf-8") as f:
        cases = json.load(f)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rundir = os.path.join(ROOT, "data", "eval_runs", run_id)
    os.makedirs(rundir, exist_ok=True)
    for name, key in (("purchase_orders.json", "purchaseOrder"), ("deliveries.json", "delivery")):
        with open(os.path.join(rundir, name), "w", encoding="utf-8") as f:
            json.dump([c[key] for c in cases], f, indent=2)

    A.PO_PATH = os.path.join(rundir, "purchase_orders.json")
    A.DELIVERIES_PATH = os.path.join(rundir, "deliveries.json")
    A.RECORDS_PATH = os.path.join(rundir, "records.json")
    A.QUEUE_PATH = os.path.join(rundir, "supervisor_queue.json")

    server = A.ThreadingHTTPServer(("127.0.0.1", 0), A.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        results = [run_case(port, c) for c in cases]
    finally:
        server.shutdown()
        server.server_close()

    passed = sum(1 for r in results if r["pass"])
    log = {
        "runId": run_id,
        "procedure": "docs/PROCEDURE.md (reviewed 2026-10-28)",
        "layout": LAYOUT,
        "actor": ACTOR,
        "modelCalls": 0,
        "cases": results,
        "summary": {
            "passed": passed,
            "failed": len(results) - passed,
            "interventions": sum(1 for r in results if r["intervention"]),
            "avgMs": round(sum(r["ms"] for r in results) / len(results), 1),
        },
    }
    with open(os.path.join(rundir, "run_log.json"), "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)
    for r in results:
        flag = "PASS" if r["pass"] else "FAIL"
        extra = f" -> {r['status']}" if "status" in r else f" ({r.get('detail', '')[:60]})"
        print(f"{flag} {r['id']}{extra} [{r['ms']}ms]")
    print(f"{passed}/{len(results)} passed, "
          f"{log['summary']['interventions']} interventions, run: data/eval_runs/{run_id}/")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
