## What changed

## Why

## Release readiness
- [ ] `ruff check .` and `pytest -q` pass locally
- [ ] Eval gate passes (`python -m evals.run_evals --llm replay`)
- [ ] Prompt or gold data changed? Responses re-recorded with `--llm record`, before and after results noted below
- [ ] Pipeline changed? `python -m pipeline.run` ends with `scenario consistency: OK` and `evals/fixtures` refreshed
- [ ] New design decision added to `docs/decision_log.md`
- [ ] No secrets, raw data or labels in the diff

## Eval results (if the agent, prompt or data changed)
| Metric | Before | After |
|---|---|---|
| Task success | | |
| Grounding | | |
| Citation validity | | |
| Safe failure | | |
