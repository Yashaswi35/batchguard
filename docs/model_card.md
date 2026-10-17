# Model card: BatchGuard deviation investigation agent

## Summary

BatchGuard drafts deviation investigation summaries for fermentation batches. A deterministic pipeline detects the deviation and lists candidate instruments with their maintenance and calibration records. Gemini chooses among supported candidates and writes the narrative. Code verifies every citation. A qualified person approves or rejects every summary.

| | |
|---|---|
| Model | `gemini-3.8-flash`, temperature 0, JSON output constrained to the `DraftSummary` schema |
| Orchestration | LangGraph state machine, 7 nodes, max 2 draft attempts |
| Prompt version | Hash of the system prompt, logged with every run (`agent/prompts.py`) |
| Owner | Yashaswi Ramachandra Reddy |
| Status | Portfolio project. Not validated for GMP use |

## Intended use

- Draft a first-pass deviation investigation for a QA investigator to review, edit and approve.
- Gather evidence: deviating variables and windows, calibration state, open and overdue work orders, relevant 21 CFR Part 211 sections.

## Out of scope

- Approving, closing or dispositioning a batch. The model cannot set status and the API requires a named human reviewer.
- Root cause analysis beyond equipment records (raw materials, operators, environment). When no equipment cause exists, the agent says so and recommends a process investigation.
- Any use on real GMP records without computer system validation (CSV) under 21 CFR Part 11.

## Inputs and outputs

- **Input:** a batch id.
- **Context given to the model:** deviating variables with z-scores and windows, candidate instruments with issues and record ids, maintenance notes as `untrusted_notes`, up to 4 retrieved CFR sections.
- **Output:** `DraftSummary` JSON (probable cause instrument or null, narrative, findings with record ids, regulatory references, recommended actions, confidence, escalation reason), wrapped by code in an `Investigation` with status.

## Evaluation

25 cases: 10 faulted batches, 10 normal batches (2 traps), 5 malicious or malformed inputs. Results in `evals/results/`.

| Metric | Rule baseline | Prompt v1 | Prompt v2 (current) | CI threshold |
|---|---|---|---|---|
| Task success, all | 100% | 88% | 100% | 85% |
| Task success, faulted | 100% | 70% | 100% | |
| Grounding | 100% | 100% | 100% | 95% |
| Citation validity | 100% | 100% | 100% | 100% |
| Safe failure | 100% | 100% | 100% | 100% |
| Verified first attempt | 100% | 90% | 90% | |

## Known failure modes

| Failure mode | Seen | Mitigation |
|---|---|---|
| Over-escalation when no cause exists | Prompt v1, batches 95, 96, 98 | Rule 5 rewritten (decision log D17) |
| Naming a cause despite conflicting records | Batch 100, first draft | Verifier rejects, retry escalates (D18) |
| Prompt injection in maintenance notes | Planted in batch 96, not followed | Untrusted field, model cannot set status, verifier rejects unretrieved citations |
| Citing a real record in the wrong sense | Batch 92, "recalibrate under CAL-00096" | Not caught. The verifier checks existence and ownership, not meaning |

## Human oversight

- Every summary ends as `PENDING_APPROVAL` or `ESCALATED`. Neither is final.
- `/approve` requires a reviewer name and a comment, works once per run and only on runs that need review.
- Every node, model call and decision is in an append-only audit log enforced by database triggers.

## Limitations

- Maintenance records are synthetic and the process is simulated (IndPenSim).
- 25 cases is a small gold set.
- The deterministic layer carries correctness. The model's added value (narrative quality, relevance of actions) is not measured directly.
