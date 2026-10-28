# Show Me Once

Learn a delivery-reconciliation workflow from a screen recording, let a person review the extracted procedure, execute it on new inputs, and verify the saved result independently.

Created October 27, 2026 for the Meta Global AI Developer Hackathon.

## Current status

Fresh project scaffold only. No model integration, browser executor, or working product yet. No API calls have been made from this project.

## Local setup

Open this folder in VS Code. Copy `.env.example` to `.env` and put your Meta API key in `.env` locally. Never paste it into chat or commit it.

```bash
cp .env.example .env
```

Git is initialized in the workspace copy. If you downloaded the ZIP, initialize your own repository:

```bash
git init -b main
git add .
git commit -m "Initialize Show Me Once hackathon project"
```

Create a public GitHub repository named `show-me-once` when ready to publish. This scaffold has not been pushed to GitHub.

## Run the receiving app (milestone 2)

```bash
cd ~/meta-hackathon/show-me-once
python3 src/app.py
```

Then open http://127.0.0.1:8000 in a browser. Layouts at `/layout-a` and `/layout-b`.
Records: `/api/records` · per-delivery history: `/api/history?deliveryId=…` · supervisor queue: `/api/supervisor-queue`.
Run checks: `python3 -m unittest discover -s tests`.
Full eval (3 agents + independent verification): `python3 scripts/run_eval.py`.

## First milestones

1. Verify Meta API access with one small request; confirm the enabled model ID in the account.
2. Build a small synthetic purchase-order and delivery app.
3. Record a matching delivery workflow with narration.
4. Extract a reviewable procedure, distinguishing observed rules from missing rules.
5. Execute on unseen deliveries and a changed layout.
6. Verify persisted records independently and compare against baselines.

## Layout

- `src/`: application implementation, to be built.
- `scripts/`: developer utilities, to be built.
- `data/fixtures/`: synthetic example records.
- `docs/`: scope, contributions, and dependency records.
- `recordings/`: local recordings; ignored by Git.

Planned primary model: Muse Spark 1.3. Validate account access before implementation. Keep credentials on the server, never in browser code.

See `docs/SCOPE.md` and `docs/CONTRIBUTIONS.md` for the build boundaries and submission record.
