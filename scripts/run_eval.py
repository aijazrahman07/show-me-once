"""Milestone 6: run all three agents on the held-out cases, verify each
independently, and compare. Every agent faces the same 9 unseen cases on
the changed layout; only the independent verdict counts as correct.

Run from the project root:  python3 scripts/run_eval.py
"""
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import agents  # noqa: E402
import app as A  # noqa: E402
import verify_run  # noqa: E402
from run_heldout import run_case  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, "data", "fixtures", "eval_cases.json")


def serve(rundir, cases, fn):
    for name, key in (("purchase_orders.json", "purchaseOrder"), ("deliveries.json", "delivery")):
        with open(os.path.join(rundir, name), "w", encoding="utf-8") as f:
            json.dump([c[key] for c in cases], f, indent=2)
    for name, empty in (("records.json", {}), ("supervisor_queue.json", [])):
        with open(os.path.join(rundir, name), "w", encoding="utf-8") as f:
            json.dump(empty, f)
    A.PO_PATH = os.path.join(rundir, "purchase_orders.json")
    A.DELIVERIES_PATH = os.path.join(rundir, "deliveries.json")
    A.RECORDS_PATH = os.path.join(rundir, "records.json")
    A.QUEUE_PATH = os.path.join(rundir, "supervisor_queue.json")
    server = A.ThreadingHTTPServer(("127.0.0.1", 0), A.Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        return fn(port)
    finally:
        server.shutdown()
        server.server_close()


def run_demo(rundir, cases):
    results = serve(rundir, cases, lambda p: [run_case(p, c) for c in cases])
    passed = sum(1 for r in results if r["pass"])
    return {"runId": os.path.basename(rundir), "agent": "demo-assisted",
            "procedure": "docs/PROCEDURE.md (reviewed 2026-10-28)",
            "layout": "layout-b", "actor": "heldout-executor",
            "modelCalls": 0, "cases": results,
            "summary": {"passed": passed, "failed": len(results) - passed,
                        "interventions": sum(1 for r in results if r["intervention"]),
                        "avgMs": round(sum(r["ms"] for r in results) / len(results), 1)}}


def run_instructed(rundir, cases):
    def go(port):
        out = []
        for c in cases:
            t0 = time.perf_counter()
            r = agents.instructed_case(port, c)
            r["ms"] = round((time.perf_counter() - t0) * 1000, 1)
            out.append(r)
        return out
    results = serve(rundir, cases, go)
    return {"runId": os.path.basename(rundir), "agent": "instructed-baseline",
            "note": "naive instruction-following; no pass claims, verifier judges",
            "layout": "layout-b", "actor": "instructed-baseline",
            "modelCalls": 0, "cases": results,
            "summary": {"attempted": len(results),
                        "interventions": sum(1 for r in results if r["intervention"]),
                        "avgMs": round(sum(r["ms"] for r in results) / len(results), 1)}}


def run_macro(rundir, cases):
    def go(port):
        t0 = time.perf_counter()
        attempts = agents.macro_agent(port)
        return attempts, round((time.perf_counter() - t0) * 1000 / max(len(attempts), 1), 1)
    attempts, avg = serve(rundir, cases, go)
    return {"runId": os.path.basename(rundir), "agent": "macro-baseline",
            "note": "replays 3 recorded steps verbatim; addresses no held-out case",
            "layout": "layout-b", "actor": "macro-baseline", "modelCalls": 0,
            "attempts": attempts,
            "cases": [{"id": c["id"], "attempted": False, "intervention": True} for c in cases],
            "summary": {"attempted": len(attempts),
                        "interventions": len(cases), "avgMs": avg}}


def main():
    with open(CASES, encoding="utf-8") as f:
        cases = json.load(f)
    eval_id = "eval_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evaldir = os.path.join(ROOT, "data", "eval_runs", eval_id)
    runners = (("demo", run_demo), ("instructed", run_instructed), ("macro", run_macro))
    rows = []
    for name, runner in runners:
        rundir = os.path.join(evaldir, name)
        os.makedirs(rundir, exist_ok=True)
        log = runner(rundir, cases)
        with open(os.path.join(rundir, "run_log.json"), "w", encoding="utf-8") as f:
            json.dump(log, f, indent=2)
        report, ok = verify_run.verify(rundir, CASES)
        with open(os.path.join(rundir, "verdict.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        rows.append({"agent": log["agent"], "dir": name,
                     "correct": report["summary"]["correct"], "total": len(cases),
                     "interventions": log["summary"]["interventions"],
                     "avgMs": log["summary"]["avgMs"], "modelCalls": 0,
                     "logCheck": report["logCheck"], "verdictOk": ok})

    comparison = {"evalId": eval_id,
                  "procedure": "docs/PROCEDURE.md (reviewed 2026-10-28)",
                  "layout": "layout-b", "cases": len(cases), "rows": rows,
                  "definitions": {
                      "correct": "persisted state matches the independently recomputed procedure outcome (rejections must be attempted and surfaced, not merely absent)",
                      "intervention": "case needing human input to reach the correct outcome",
                      "avgMs": "mean HTTP round-trip ms per case (macro: per replayed step)",
                      "modelCalls": "model API calls made by the agent (all procedural: 0)"},
                  "limitations": [
                      "Single run, 9 held-out cases; no statistical claims.",
                      "Baselines are documented procedural stubs, not LLM agents.",
                      "Verifier is a separate implementation of the same written procedure, not a second spec author."]}
    with open(os.path.join(evaldir, "comparison.json"), "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2)
    lines = [f"# Eval comparison — {eval_id}", "",
             f"Procedure: reviewed 2026-10-28 · layout: layout-b · cases: {len(cases)}", "",
             "| agent | independently correct | interventions | avg ms | model calls |",
             "| --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append(f"| {r['agent']} | {r['correct']}/{r['total']} | {r['interventions']} "
                     f"| {r['avgMs']} | {r['modelCalls']} |")
    lines += ["", "## Reading",
              "- demo-assisted: follows the reviewed procedure; M4 rejects surface to a human.",
              "- instructed-baseline: guesses blank as zero, self-confirms zero, abandons rejects.",
              "- macro-baseline: replays 3 recorded steps against unknown IDs; resolves nothing.",
              "", "Metric definitions and limitations: see comparison.json."]
    with open(os.path.join(evaldir, "COMPARISON.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"eval: data/eval_runs/{eval_id}/")
    for r in rows:
        print(f"{r['agent']:20s} {r['correct']}/{r['total']} correct, "
              f"{r['interventions']} interventions, {r['avgMs']}ms avg "
              f"(log: {r['logCheck']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
