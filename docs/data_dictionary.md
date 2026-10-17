# Data dictionary

Gold tables are the data products the agent reads. They are built by `pipeline/gold.py` (pandas) and `lakehouse/batchguard_medallion.py` (Databricks Delta, parity verified). A frozen copy lives in `evals/fixtures/gold/`. Ground-truth fault labels are dropped in silver and never appear here.

## gold.batch_summary

One row per batch (100 rows).

| Column | Type | Meaning |
|---|---|---|
| batch_id | int | IndPenSim batch number, 1 to 100 |
| reactor_id | string | BR-101 (odd batches) or BR-102 (even batches) |
| start_ts, end_ts | timestamp | Simulated batch start and end on the campaign calendar |
| control_strategy | string | recipe (1-30), operator (31-60), apc_raman (61-90), faulted (91-100) |
| pen_harvested_during_kg, pen_harvested_end_kg, pen_yield_total_kg | float | Penicillin yield from the IndPenSim statistics file |
| duration_h | float | Batch length in hours |
| final_penicillin_g_l | float | Penicillin concentration at the last timestep |
| dq_failed_rows | int | Rows in this batch that failed an error-level data quality check |
| deviation_flag | bool | True when at least one excursion reaches max abs z of 20 or more |
| n_significant_events | int | Number of excursions at abs z of 20 or more |
| max_abs_z | float | Largest abs z across significant events, 0 if none |
| deviating_variables | JSON string | Variables with significant events, highest z first |
| window_start_ts, window_end_ts | timestamp | Investigation window: first to last significant event, or the whole batch |

## gold.deviation_events

One row per contiguous excursion of one variable outside the golden-batch envelope (582 rows).

| Column | Type | Meaning |
|---|---|---|
| event_id | string | `EV-<batch>-<n>`, numbered by start time within the batch |
| batch_id | int | Batch |
| variable | string | One of the 13 monitored process variables in `config/equipment_map.json` |
| start_h, end_h | float | First and last hour of the excursion |
| duration_h | float | end_h minus start_h plus one 0.2 h step, at least 1.0 |
| start_ts, end_ts | timestamp | Same, on the campaign calendar |
| max_abs_z | float | Largest abs z in the excursion, 2 decimals |
| direction | string | high or low, the sign of z at the peak |
| peak_value | float | Variable value at the peak, in its own units |
| significant | bool | max_abs_z of 20 or more |

## gold.equipment_context

One row per batch and instrument on that batch's reactor (100 x 10 = 1,000 rows). Lists are JSON strings so the table is portable between parquet and Delta.

| Column | Type | Meaning |
|---|---|---|
| batch_id, reactor_id | int, string | Batch and its reactor |
| equipment_id | string | e.g. PH-102, MFC-101 |
| equipment_type, description, criticality | string | Instrument master data |
| linked_variables | JSON string | Process variables this instrument affects |
| linked_to_deviation | bool | True if any linked variable is in deviating_variables |
| last_cal_ids | JSON string | Calibration record ids on the latest calibration date before the window (two ids when records conflict) |
| last_cal_date, cal_due_date | timestamp | Latest calibration and its due date (90 day interval) |
| last_cal_result | string | PASS, FAIL or CONFLICT |
| last_cal_note | string | As-found note from the calibration record |
| cal_overdue | bool | Due date before the window start |
| cal_days_overdue | int | Days past due at the window start |
| open_corrective_wos | JSON string | Corrective work orders open during the window: wo_id, opened_ts, description, notes |
| overdue_pm_wos | JSON string | Preventive work orders past due and not closed at the window start |
| recent_closed_wos | JSON string | Work orders closed in the 7 days before the window. Notes are passed to the agent as untrusted text |

## gold.envelope

Mean and standard deviation per 0.2 h timestep and variable from batches 1-90 (local: wide, `<variable>__mean` and `<variable>__std`. Databricks: long, one row per timestep and variable). Standard deviation is floored at 10% of the variable's global standard deviation. Timesteps with fewer than 10 reference batches are null and never scored.

## silver.dq_failures

| Column | Meaning |
|---|---|
| table | process, calibrations or work_orders |
| check | e.g. `range:ph`, `duplicate:batch_id+time_h`, `conflict:cal_result` |
| severity | error (row gets dq_pass = false) or warn |
| batch_id, time_h, record_key | Where the failure is |
| detail | Plain-language description |
