"""Run the medallion pipeline end to end: python -m pipeline.run"""
import json

import pandas as pd

from . import bronze, gold, silver
from . import config as C


def report(lake):
    """Detection quality against the bronze labels. Reporting only, labels never reach gold."""
    labels = pd.read_parquet(lake / "bronze" / "batches.parquet")[["batch_id", "is_faulted"]]
    s = pd.read_parquet(lake / "gold" / "batch_summary.parquet").merge(labels, on="batch_id")
    ev = pd.read_parquet(lake / "gold" / "deviation_events.parquet")
    per_batch = ev.groupby("batch_id").max_abs_z.max().reindex(s.batch_id).fillna(0)
    s["max_event_z"] = per_batch.values
    normal, faulted = s[s.is_faulted == 0], s[s.is_faulted == 1]
    tp = int((faulted.deviation_flag).sum())
    fp = int((normal.deviation_flag).sum())
    print(f"\nDetection at Z_SIGNIFICANT={C.Z_SIGNIFICANT}: caught {tp}/10 faulted batches, "
          f"{fp}/90 normal batches flagged")
    print("highest max z in normal batches:",
          normal.nlargest(5, "max_event_z")[["batch_id", "control_strategy", "max_event_z"]].to_dict("records"))
    print("lowest max z in faulted batches:",
          faulted.nsmallest(3, "max_event_z")[["batch_id", "max_event_z"]].to_dict("records"))
    print("\nfaulted batch windows and deviating variables:")
    print(faulted[["batch_id", "reactor_id", "window_start_ts", "window_end_ts", "deviating_variables"]]
          .to_string(index=False))


def equipment_issues(row):
    """Flags that make an instrument a candidate cause. Shared with the scenario consistency check."""
    f = []
    if row.cal_overdue:
        f.append(f"calibration overdue {row.cal_days_overdue}d")
    if row.last_cal_result in ("FAIL", "CONFLICT"):
        f.append(f"last calibration {row.last_cal_result}")
    if json.loads(row.open_corrective_wos):
        f.append("open corrective work order")
    if json.loads(row.overdue_pm_wos):
        f.append("overdue preventive maintenance")
    return f


def check_scenarios(lake):
    """Gold data must match the planted answer key, or agent evals would be measuring data bugs."""
    ctx = pd.read_parquet(lake / "gold" / "equipment_context.parquet")
    summary = pd.read_parquet(lake / "gold" / "batch_summary.parquet").set_index("batch_id")
    scenarios = json.loads((C.REPO / "evals" / "planted_scenarios.json").read_text())
    problems = []
    for s in scenarios:
        b = s["batch_id"]
        if bool(summary.deviation_flag[b]) != s["deviation_expected"]:
            problems.append(f"batch {b}: deviation_flag={summary.deviation_flag[b]}, expected {s['deviation_expected']}")
        rows = ctx[(ctx.batch_id == b) & ctx.linked_to_deviation]
        flagged = sorted(r.equipment_id for r in rows.itertuples() if equipment_issues(r))
        expected = [s["expected_cause_equipment"]] if s["expected_cause_equipment"] else []
        if s["expect_escalation"]:
            continue  # conflicting records: the flagged instrument is ambiguous by design
        if flagged != expected:
            problems.append(f"batch {b} ({s['kind']}): linked instruments with issues {flagged}, expected {expected}")
    print("\nscenario consistency:", "OK, all planted scenarios match gold data" if not problems else "")
    for p in problems:
        print("  PROBLEM:", p)
    return problems


def main():
    print("bronze:", bronze.run(C.DATA_DIR, C.LAKE))
    print("silver:", json.dumps(silver.run(C.LAKE), indent=1))
    print("gold:", gold.run(C.LAKE))
    report(C.LAKE)
    if check_scenarios(C.LAKE):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
