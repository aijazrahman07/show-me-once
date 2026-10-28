# Recording script — delivery reconciliation demo (milestone 3)

Target: one continuous screen recording, ~3 minutes, with voiceover.
Record on **Layout A only** — Layout B stays held out for the
generalization test in milestone 5.

## Setup checklist

- [ ] App running: `python3 src/app.py` in `~/meta-hackathon/show-me-once`
- [ ] Browser open at http://127.0.0.1:8000/layout-a (fresh, no prior records)
- [ ] Screen recorder capturing browser window + microphone
- [ ] `.env` and terminal **closed or off-screen** — never show the API key
- [ ] Quiet room; speak the lines below close to verbatim

## Script

### 0:00–0:25 — Intro (browser on Layout A)

> "This is the Show Me Once receiving app. I reconcile incoming deliveries
> against their purchase orders. The rule I follow: if the received count
> equals the ordered count, it's a match. If fewer arrived, it's a shortage.
> If the count is missing, I flag it for a person — I don't guess."

### 0:25–1:05 — Case 1: match (delivery-001)

- Select `delivery-001 (PO-001)`. Point at: "PO-001: Keyboard, ordered 10."
- Quantity reads 10. Say: "Ten ordered, ten received — that's a match."
- Note field: type `sealed cartons`.
- Click **Reconcile**. Point at `"status": "match"` in the result.
- Say: "Saved. One record stored."

### 1:05–1:50 — Case 2: shortage (delivery-002)

- Select `delivery-002 (PO-002)`. Say: "PO-002, ten ordered."
- Quantity reads 8. Say: "Only eight arrived — that's a shortage, not a match."
- Note field: type `2 cartons missing from pallet`.
- Click **Reconcile**. Point at `"status": "shortage"`.
- Say: "Saved as a shortage. The note is just a note — it never
  changes the outcome on its own."

### 1:50–2:30 — Case 3: missing quantity (delivery-003)

- Select `delivery-003 (PO-003)`. Clear the quantity field.
- Say: "No count on this delivery slip. I have no rule for a missing
  count, so I won't invent one — I flag it for human review."
- Click **Reconcile**. Point at `"status": "missing_quantity"`.
- Say: "Saved as missing quantity. A person decides what happens next."

### 2:30–3:00 — Verify saved state

- Scroll to **Saved records**. Say: "Three deliveries, three saved
  records: one match, one shortage, one flagged as missing."
- Briefly open `/api/records` in a second tab showing the same JSON.
- Say: "The saved state is what counts — not what I claim, but what's stored."
- Stop recording.

## After recording

1. Save the file as `recordings/reconciliation-v1.<ext>` (stays local; git-ignored).
2. Watch it once: narration audible, no key on screen, all three statuses visible.
3. Log the filename and duration in `docs/CONTRIBUTIONS.md`.
