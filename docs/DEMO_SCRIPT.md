# Demo video script — 3 minutes (submission requirement)

Goal: show the team's NEW work clearly in under 3:00. Record at 1080p,
terminal font 16pt+, mic on. NEVER show `.env` or the terminal where the
key was pasted. Rehearse once with a timer before the take.

## Shot plan

### 0:00–0:20 — Problem (slides or talking head)

> "AI agents can watch a screen recording — but can they work a new case on
> a changed screen, know what they don't know, and prove what they saved?
> Show Me Once: record once, review the procedure, execute on unseen input,
> verify independently."

### 0:20–0:50 — The recording (clip from first recording, sped 1.25x ok)

Show Layout A reconciling the 3 demo deliveries. Voiceover:

> "I recorded one delivery-reconciliation workflow: a match, a shortage,
> and a missing count flagged for a human. Thirty seconds of demo,
> following the script in docs."

### 0:50–1:20 — Reviewed procedure (show docs/PROCEDURE.md)

Scroll: 5 observed rules, then the 6 resolved gaps.

> "The extraction separates what the demo showed from what it didn't.
> Six gaps — overages, shortages, invalid counts, rework rules — each
> resolved by a person before any execution. The agent is never allowed
> to invent these answers."

### 1:20–2:10 — Live execution on unseen input (Layout B + terminal)

Run: `python3 scripts/run_heldout.py`. Show PASS lines, then open
`/api/records` and `/api/supervisor-queue` on Layout B.

> "Nine brand-new deliveries on a changed layout the demo never showed.
> Nine out of nine correct. Three cases needed a human — zero self-confirm,
> a negative, a fraction — and the agent surfaced all three instead of
> guessing. The embedded instruction to approve a mismatch was ignored."

### 2:10–2:40 — Independent verification (terminal)

Run: `python3 scripts/run_eval.py`. Show the comparison table.

> "A separate verifier recomputes every outcome from the procedure — no
> self-reported success counts. Demo-assisted: nine of nine. Naive
> instruction-following: six. Blind macro replay: zero."

### 2:40–3:00 — Close (repo on screen)

Show github.com repo page.

> "All new during the event: app, procedure, evals, and verification.
> Repository link below. Track: [confirm: Indie Developer / Push the
> Frontier]. Thank you."

## Checklist

- [ ] Under 3:00 including title cards
- [ ] No API key, `.env`, or key-paste terminal visible in any frame
- [ ] New work explicitly named (app, procedure, held-out eval, verifier)
- [ ] Repo URL in description + closing frame
- [ ] Track selection confirmed in registration before submitting
