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

**D6. Two thresholds: |z| > 3 marks an excursion, max |z| >= 20 makes it a deviation.**
Excursions shorter than 1 hour are ignored, and gaps of up to 2 in-range points are bridged. On IndPenSim the highest max z in any normal batch was 9.4 and the lowest in a faulted batch was 42.3. A first threshold of 10 sat too close to normal variation, so it was raised to 20, a 2x margin on both sides. Result: 10/10 faulted batches flagged, 0/90 normal batches flagged.

**D7. Synthetic CMMS with planted scenarios.**
No public CMMS data exists for this process. Records are seeded and simulated. Each fault pattern has a batch with an equipment cause and a twin batch without one, so the agent cannot infer the cause from symptoms alone.

**D8. Pipeline run fails if gold data disagrees with the planted answer key.**
The first check found a work order planted for batch 97 that overlapped batch 95's window by 2 hours, which would have marked the agent wrong for a data bug. Corrective work orders now open 2 days, not 4, before the fault, and `python -m pipeline.run` exits non-zero on any mismatch.

**D9. Data quality failures are flagged, not dropped.**
Silver keeps every row with a `dq_pass` flag and writes failures to `dq_failures`. Failed rows are excluded from the reference envelope but remain visible for investigation. In GxP data, silently deleting records is worse than flagging them.
