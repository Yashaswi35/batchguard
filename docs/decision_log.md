# Decision Log

Dated record of design decisions and the reasoning behind them.

## 2026-10-16

**D1. Assign IndPenSim column names by position, not from the header.**
The raw header has 2,239 names but data rows have 2,237 values because a label containing a comma was split. Reading the header misassigned Raman intensities to `batch_id` (47,361 "batches"). Names are assigned by position, and extraction asserts exactly 100 batches, `batch_id == batch_ref`, and agreement with the official statistics file.

**D2. Fault windows come from `fault_ref`, not `fault_flag`.**
`fault_flag` is a batch-level label (1 for every row of a faulted batch). `fault_ref` switches per timestep and marks the actual fault episodes.

**D3. Ground-truth labels stop at bronze.**
`fault_ref`, `fault_flag` and `is_faulted` are dropped in silver, so neither gold tables nor the agent can see the answer. Labels are used only in the pipeline's detection report and in evaluation.

**D4. Golden-batch envelope: mean and std per 0.2 h timestep from batches 1-90.**
These are the batches released without deviation. A timestep needs at least 10 reference batches to be judged, so late timesteps reached by only a few batches are not scored on noise.

**D5. Floor each timestep's std at 10% of the variable's global std.**
Variables that are almost always zero in normal batches (heating water flow) produced z-scores above 10^6. The floor keeps z-scores meaningful without hiding real excursions.

**D6. Two thresholds: |z| > 3 marks an excursion, max |z| >= 10 makes it a deviation.**
Excursions shorter than 1 hour are ignored, and gaps of up to 2 in-range points are bridged. The 10 threshold was chosen by comparing the highest max z in normal batches against the lowest in faulted batches (see pipeline run report).

**D7. Synthetic CMMS with planted scenarios.**
No public CMMS data exists for this process. Records are seeded and simulated. Each fault pattern has a batch with an equipment cause and a twin batch without one, so the agent cannot infer the cause from symptoms alone.

**D8. Pipeline run fails if gold data disagrees with the planted answer key.**
The first check found a work order planted for batch 97 that overlapped batch 95's window by 2 hours, which would have marked the agent wrong for a data bug. Corrective work orders now open 2 days, not 4, before the fault, and `python -m pipeline.run` exits non-zero on any mismatch.

**D9. Data quality failures are flagged, not dropped.**
Silver keeps every row with a `dq_pass` flag and writes failures to `dq_failures`. Failed rows are excluded from the reference envelope but remain visible for investigation. In GxP data, silently deleting records is worse than flagging them.

**D10. The model drafts, code decides.**
Deviation detection, candidate instruments and their issues come from deterministic gold tables. The LLM only chooses among supported candidates and writes the narrative. A verifier checks every cited record id, instrument and regulation section against what the tools returned. Failed drafts get one retry with the specific errors, then escalate to a human. The model never sets the status.

**D11. Maintenance log notes are untrusted input.**
Free-text work order notes go into a separate `untrusted_notes` field, never mixed with facts, and the prompt states they are data, not instructions. A prompt injection is planted in batch 96 to test this. The verifier rejects any regulation section that was not retrieved, so an injected citation cannot pass.

**D12. Regulation text comes from the official eCFR API, not model memory.**
`data/fetch_regs.py` pulls 21 CFR Part 211 by section with its as-of date. Retrieval is a small BM25 index, and 211.192 (production record review) is always included because every deviation requires a documented investigation.

**D13. Append-only audit trail enforced by the database.**
Every graph node writes inputs hash, outputs, model and prompt version to SQLite. Triggers reject UPDATE and DELETE on the audit and approval tables, so the record cannot be edited after the fact.

**D14. Record and replay model responses for CI.**
Live Gemini responses are saved, keyed by model, prompt version and full prompt. CI replays them, so evaluations test real model outputs with no API key and no randomness. If a prompt or the data changes, the key changes and CI fails until responses are re-recorded.

**D15. eCFR API requires compressed responses.**
The versioner endpoint returns HTTP 406 unless the client sends `Accept-Encoding: gzip`, and Python's urllib does not decompress on its own. The fetcher requests gzip and decompresses explicitly. Found by reading the API's error body rather than guessing at headers.

**D16. Model: gemini-3.8-flash.**
gemini-2.5-flash returned 404 for new API keys. The model name is set in one place (`agent/llm.py`, overridable with `GEMINI_MODEL`) and is part of the replay key, so changing it forces responses to be re-recorded.
