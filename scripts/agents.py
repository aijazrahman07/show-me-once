"""Baseline agents for the milestone 6 comparison.

Procedural stubs with fully documented behaviors — no model calls. Actual
results are recorded by scripts/run_eval.py; nothing here is claimed to
behave like an LLM.

- macro_agent: replays the three recorded actions verbatim. It knows only
  delivery-001/002/003, so on unseen deliveries it cannot address any case.
- instructed_case: naive instruction-following without the demo procedure:
  blank counts are guessed as zero, zero is self-confirmed, invalid inputs
  are submitted as-is and abandoned on rejection, corrections are retried
  without a reason and abandoned on rejection. Notes pass through verbatim.
"""
import json
import urllib.error
import urllib.request

LAYOUT = "layout-b"

RECORDED_STEPS = [
    {"deliveryId": "delivery-001", "receivedQuantity": 10, "note": "sealed cartons"},
    {"deliveryId": "delivery-002", "receivedQuantity": 8, "note": "2 cartons missing from pallet"},
    {"deliveryId": "delivery-003", "receivedQuantity": None, "note": ""},
]


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


def macro_agent(port):
    """Replay the recording's three steps verbatim; return raw attempts."""
    attempts = []
    for step in RECORDED_STEPS:
        status, body = api(port, "POST", "/api/reconcile",
                           {**step, "layout": LAYOUT, "actor": "macro-baseline"})
        attempts.append({"sent": step, "http": status,
                         "detail": body.get("status", body.get("error", ""))})
    return attempts


def instructed_case(port, case):
    """One held-out case with naive instruction-following (no demo procedure)."""
    did = case["delivery"]["id"]
    qty = case["input"].get("receivedQuantity")
    submitted = {"deliveryId": did, "note": case["input"].get("note", ""),
                 "layout": LAYOUT, "actor": "instructed-baseline"}
    if qty is None:
        submitted["receivedQuantity"] = 0  # naive guess: blank means zero
    elif qty == 0:
        submitted["receivedQuantity"] = 0
        submitted["confirmZero"] = True  # naive: confirm it yourself
    else:
        submitted["receivedQuantity"] = qty
    status, body = api(port, "POST", "/api/reconcile", submitted)
    result = {"id": case["id"], "submitted": submitted, "http": status,
              "intervention": False, "modelCalls": 0}
    if status != 200:
        result["intervention"] = True  # rejected; naive agent gives up
        result["detail"] = body.get("error", "")
        return result
    result["status"] = body.get("status")
    if "correct" in case and not body.get("resolved"):
        retry = {"deliveryId": did, "receivedQuantity": case["correct"]["receivedQuantity"],
                 "note": case["correct"].get("note", ""), "layout": LAYOUT,
                 "actor": "instructed-baseline"}  # naive: no correction reason
        cstatus, cbody = api(port, "POST", "/api/reconcile", retry)
        result["correctHttp"] = cstatus
        if cstatus != 200:
            result["intervention"] = True  # rejected; naive agent gives up
            result["detail"] = cbody.get("error", "")
    return result
