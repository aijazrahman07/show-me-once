"""Milestone 6: independently verify an eval run's persisted state.

Deliberately does NOT import the app: it reimplements the rules from
docs/PROCEDURE.md (reviewed 2026-10-28) and judges what was actually saved.
An agent's self-reported success never stands in for this verdict.

Checks per case: saved record matches the recomputed correct state, the
fixture's own expectation matches the recomputation, queue entries are
consistent, revision history is intact. Cross-checks any pass claims in
run_log.json against the recomputed verdict.

Limitation: separate implementation, same written procedure — a second
implementation of one spec, not a second spec author.

CLI:  python3 scripts/verify_run.py <run_dir>   (exit 1 on any mismatch)
"""
import json
import os
import re
import sys

SHORTAGE = "Discrepancy\u2014shortage"
OVERAGE = "Discrepancy\u2014overage"


class Reject(Exception):
    pass


def validate_qty(raw):
    """M4: whole units only. Returns int or None (blank). Rejects otherwise."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return None
    if isinstance(raw, bool):
        raise Reject("non-integer quantity")
    if isinstance(raw, int):
        qty = raw
    elif isinstance(raw, float):
        if not raw.is_integer():
            raise Reject("fractional quantity")
        qty = int(raw)
    elif isinstance(raw, str):
        if not re.fullmatch(r"-?\d+", raw.strip()):
            raise Reject("non-numeric quantity")
        qty = int(raw.strip())
    else:
        raise Reject("non-integer quantity")
    if qty < 0:
        raise Reject("negative quantity")
    return qty


def classify(ordered, received):
    if received is None:
        return "missing_quantity", {"type": "missing_quantity"}, False
    if received == ordered:
        return "match", None, True
    if received < ordered:
        return SHORTAGE, {"type": "shortage", "missing": ordered - received}, False
    return OVERAGE, {"type": "overage", "excess": received - ordered}, False


def recompute_initial(case):
    """Correct state after the first input only."""
    ordered = case["purchaseOrder"]["orderedQuantity"]
    try:
        qty = validate_qty(case["input"].get("receivedQuantity"))
    except Reject:
        return ("reject",)
    if qty == 0 and not case["input"].get("confirmZero"):
        return ("reject",)
    status, disc, resolved = classify(ordered, qty)
    return ("save", status, disc, resolved, not resolved)


def recompute(case):
    """Correct final state from case input per the reviewed procedure.

    Returns ("reject",) or ("save", status, discrepancy, resolved, queued,
    revision, queue_resolved). The correct flow never self-confirms zero and
    always supplies a correction reason, so rejections and corrections are
    fully determined by the input.
    """
    init = recompute_initial(case)
    if init[0] == "reject" or "correct" not in case:
        if init[0] == "reject":
            return ("reject",)
        _, status, disc, resolved, queued = init
        return ("save", status, disc, resolved, queued, 1, False)
    ordered = case["purchaseOrder"]["orderedQuantity"]
    cqty = validate_qty(case["correct"]["receivedQuantity"])
    cstatus, cdisc, cresolved = classify(ordered, cqty)
    return ("save", cstatus, cdisc, cresolved, True, 2, cresolved)


def check_fixture(case, want):
    """The fixture's own expectation must match the recomputation."""
    exp = case["expected"]
    if "correct" in case:
        init = recompute_initial(case)
        if init[0] != "save":
            return False
        _, s, d, r, q = init
        ok = exp.get("status") == s and exp.get("resolved") is r and exp.get("queued") is q
        if "missing" in exp:
            ok = ok and (d or {}).get("missing") == exp["missing"]
        if "excess" in exp:
            ok = ok and (d or {}).get("excess") == exp["excess"]
        want2 = case["expectedAfterCorrect"]
        return ok and want[1] == want2["status"] and want[3] is want2["resolved"] \
            and want[5] == want2["revision"] and want[6] is want2["queueResolved"]
    if want[0] == "reject":
        return exp.get("http") == 400
    _, status, disc, resolved, queued, _, _ = want
    ok = (exp.get("status") == status and exp.get("resolved") is resolved
          and exp.get("queued") is queued)
    if "missing" in exp:
        ok = ok and (disc or {}).get("missing") == exp["missing"]
    if "excess" in exp:
        ok = ok and (disc or {}).get("excess") == exp["excess"]
    return ok


def check_history(entry):
    """Revisions sequential; previous values preserved; reasons present."""
    current, history = entry["current"], entry.get("history", [])
    revs = [h.get("revision") for h in history] + [current.get("revision")]
    if revs != list(range(1, len(revs) + 1)):
        return False, "revision sequence broken"
    if current.get("revision", 1) > 1:
        if not (current.get("correctionReason") or "").strip():
            return False, "revision without correction reason"
        prev = {k: history[-1].get(k) for k in
                ("receivedQuantity", "status", "note", "reconciledAt")}
        if current.get("previousValues") != prev:
            return False, "previousValues do not match prior revision"
    return True, ""


def verify(rundir, cases_path):
    with open(cases_path, encoding="utf-8") as f:
        cases = json.load(f)
    with open(os.path.join(rundir, "records.json"), encoding="utf-8") as f:
        records = json.load(f)
    with open(os.path.join(rundir, "supervisor_queue.json"), encoding="utf-8") as f:
        queue = json.load(f)
    log = {}
    log_path = os.path.join(rundir, "run_log.json")
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            log = json.load(f)

    logged = {c.get("id"): c for c in log.get("cases", [])}
    verdicts = []
    for case in cases:
        did, issues = case["delivery"]["id"], []
        want = recompute(case)
        if not check_fixture(case, want):
            issues.append("fixture expectation disagrees with recomputed procedure")
        entry = records.get(did)
        mine = [q for q in queue if q.get("deliveryId") == did]
        if want[0] == "reject":
            if entry is not None:
                issues.append("record persisted despite required rejection")
            if mine:
                issues.append("queue entry despite required rejection")
            if logged.get(case["id"], {}).get("http") != 400:
                issues.append("rejection never attempted/surfaced by the agent")
        else:
            _, status, disc, resolved, queued, revision, qresolved = want
            if entry is None:
                issues.append("no record persisted")
            else:
                cur = entry["current"]
                if cur.get("status") != status:
                    issues.append(f"status {cur.get('status')!r} != {status!r}")
                if cur.get("resolved") is not resolved:
                    issues.append("resolved flag wrong")
                if cur.get("discrepancy") != disc:
                    issues.append("discrepancy wrong")
                if cur.get("revision") != revision:
                    issues.append(f"revision {cur.get('revision')} != {revision}")
                if cur.get("note") != case["input"].get("note", "") and "correct" not in case:
                    issues.append("note not verbatim")
                if "correct" in case and cur.get("note") != case["correct"].get("note", ""):
                    issues.append("corrected note not verbatim")
                ok, why = check_history(entry)
                if not ok:
                    issues.append(why)
            pending = [q for q in mine if q.get("status") == "pending"]
            if queued and not resolved and not pending:
                issues.append("unresolved outcome missing from supervisor queue")
            if qresolved and any(q.get("status") != "resolved" for q in mine):
                issues.append("queue entry not resolved after correction")
            if not queued and mine:
                issues.append("spurious queue entry for clean match")
        verdicts.append({"id": case["id"], "correct": not issues, "issues": issues})

    # Cross-check self-reported pass claims against the independent verdict.
    log_check = "no pass claims in log"
    if log.get("cases") and any("pass" in c for c in log["cases"]):
        claimed = {c["id"] for c in log["cases"] if c.get("pass")}
        actual = {v["id"] for v in verdicts if v["correct"]}
        log_check = "claims match" if claimed == actual else \
            f"MISMATCH claimed={sorted(claimed)} actual={sorted(actual)}"

    correct = sum(1 for v in verdicts if v["correct"])
    return {"verdicts": verdicts, "logCheck": log_check,
            "summary": {"correct": correct, "wrong": len(verdicts) - correct}}, \
        correct == len(verdicts) and log_check in ("claims match", "no pass claims in log")


def main(argv):
    rundir = argv[1]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    report, ok = verify(rundir, os.path.join(root, "data", "fixtures", "eval_cases.json"))
    for v in report["verdicts"]:
        flag = "CORRECT" if v["correct"] else "WRONG"
        extra = "" if v["correct"] else f" ({'; '.join(v['issues'])})"
        print(f"{flag} {v['id']}{extra}")
    print(f"log check: {report['logCheck']}")
    s = report["summary"]
    print(f"{s['correct']}/{s['correct'] + s['wrong']} independently correct")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
