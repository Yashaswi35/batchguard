# Runbook

How to run, change, monitor and recover BatchGuard.

## Run

| Task | Command |
|---|---|
| Tests and lint (no data, no key) | `ruff check . && python -m pytest -q` |
| Rebuild data and gold tables | `python data/prepare_process_data.py <csv> <stats_csv> ~/Projects/batchguard_data && python data/generate_cmms.py ~/Projects/batchguard_data && python -m pipeline.run` |
| Refresh regulation text | `python data/fetch_regs.py` |
| Investigate one batch | `python -m agent.investigate 92 --llm gemini` (or `rule`, `replay`) |
| API locally | `uvicorn api.main:app --reload`, docs at http://127.0.0.1:8000/docs |
| API in Docker | `docker build -t batchguard . && docker run -p 8000:8000 -v batchguard-data:/data batchguard` |
| API in Docker with Gemini | add `-e BATCHGUARD_LLM=gemini -e GOOGLE_API_KEY=...` |
| Eval, what CI runs | `python -m evals.run_evals --llm replay` |

## Change safely

**Changing the prompt or the model**
1. Edit `agent/prompts.py` or `GEMINI_MODEL`. The prompt version and replay keys change automatically.
2. Keep the old results: `cp evals/results/gemini.json evals/results/gemini_<label>.json`.
3. `rm evals/recordings/*.json && python -m evals.run_evals --llm record` (about 11 calls, under $0.10).
4. `python -m pytest -q` must pass, then open a PR with before and after numbers in the template.

**Changing the pipeline or thresholds**
1. Edit `pipeline/config.py`. Record the reason and the numbers in `docs/decision_log.md`.
2. `python -m pipeline.run` must end with `scenario consistency: OK`. It exits non-zero otherwise.
3. `python -m evals.freeze_fixture`, then re-record as above, because the agent's context changed.
4. Re-run the Databricks notebook. Its last cell must print `PARITY OK`.

**Adding an evaluation case**
Plant it in `data/generate_cmms.py`, regenerate, add it to `evals/build_gold_set.py`, rebuild the gold set, then re-record.

## Monitor

- Every run writes to `audit.db` (`BATCHGUARD_DATA`): one row per node, plus the final investigation and any approval.
- Escalation rate: `SELECT status, COUNT(*) FROM investigations GROUP BY status`. A rising ESCALATED share with unchanged data points to a model or prompt regression.
- Verifier rejections: `SELECT output_json FROM audit_log WHERE node = 'verify' AND output_json NOT LIKE '%"errors": []%'`.

## Recover

| Symptom | Likely cause | Action |
|---|---|---|
| CI red on the eval gate with `No recorded response` | Prompt, model or gold data changed without re-recording | Re-record (above) |
| CI red on metrics | A change lowered task success, grounding, citation validity or safe failure | Do not merge. Read `first_errors` in the eval output |
| `429 RESOURCE_EXHAUSTED` | Gemini quota | Wait, or use the paid tier. CI is unaffected |
| `404` on the model | Model retired | Change `GEMINI_MODEL`, re-record, note it in the decision log |
| eCFR fetch `406` | Missing gzip | Already handled. Check `Accept-Encoding` if the fetcher was edited |
| Bad release on main | Any | `git revert <sha>` in a PR. History and the audit log are never rewritten |

## Secrets

- `GOOGLE_API_KEY` lives only in `.env` (git-ignored, see `.env.example`) or a container environment variable.
- CI never needs the key. It replays recordings.
- Rotate the key in Google AI Studio if it is ever shared or committed.
