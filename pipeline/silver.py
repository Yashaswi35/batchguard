"""Silver: typed, conformed, validated. Rows failing error-level checks are flagged, not dropped."""
import pandas as pd

from . import config as C
from . import dq


def conform_process(proc, sched):
    proc = proc.drop(columns=[c for c in C.LABEL_COLS if c in proc], errors="ignore")
    proc = proc.merge(sched[["batch_id", "reactor_id", "start_ts"]], on="batch_id", how="left")
    proc["event_ts"] = proc["start_ts"] + pd.to_timedelta(proc["time_h"], unit="h")
    proc["t"] = proc["time_h"].round(1)
    proc["temperature_c"] = proc["temperature_k"] - 273.15
    return proc.drop(columns=["start_ts"])


def validate_process(proc, sched):
    fails = pd.concat([
        dq.check_not_null(proc, C.REQUIRED_PROCESS_COLS, "process"),
        dq.check_range(proc, C.RANGE_RULES, "process"),
        dq.check_duplicates(proc, ["batch_id", "time_h"], "process"),
        dq.check_monotonic_time(proc, "process"),
        dq.check_referential(proc, "batch_id", sched["batch_id"], "process", None),
    ], ignore_index=True)
    bad = fails[fails.severity == "error"][["batch_id", "time_h"]].drop_duplicates()
    bad["_bad"] = True
    proc = proc.merge(bad, on=["batch_id", "time_h"], how="left")
    proc["dq_pass"] = proc.pop("_bad").isna()
    return proc, fails


def validate_cmms(equip, cals, wos):
    fails = pd.concat([
        dq.check_not_null(cals, ["cal_id", "equipment_id", "cal_date", "result"], "calibrations", "cal_id"),
        dq.check_not_null(wos, ["wo_id", "equipment_id", "opened_ts", "wo_type"], "work_orders", "wo_id"),
        dq.check_duplicates(cals, ["cal_id"], "calibrations", "cal_id"),
        dq.check_duplicates(wos, ["wo_id"], "work_orders", "wo_id"),
        dq.check_referential(cals, "equipment_id", equip["equipment_id"], "calibrations", "cal_id"),
        dq.check_referential(wos, "equipment_id", equip["equipment_id"], "work_orders", "wo_id"),
        dq.check_conflicting_results(cals),
    ], ignore_index=True)
    return fails


def run(lake):
    src = lake / "bronze"
    out = lake / "silver"
    out.mkdir(parents=True, exist_ok=True)
    read = lambda n: pd.read_parquet(src / f"{n}.parquet").drop(columns=["_ingest_ts", "_source_file"])

    sched = read("schedule")
    for c in ["start_ts", "end_ts"]:
        sched[c] = pd.to_datetime(sched[c])
    batches = read("batches").drop(columns=["is_faulted"])  # label stays in bronze
    equip, cals, wos = read("equipment"), read("calibrations"), read("work_orders")
    for c in ["cal_date", "due_date"]:
        cals[c] = pd.to_datetime(cals[c])
    for c in ["opened_ts", "due_ts", "closed_ts"]:
        wos[c] = pd.to_datetime(wos[c])

    proc = conform_process(read("process"), sched)
    proc, proc_fails = validate_process(proc, sched)
    cmms_fails = validate_cmms(equip, cals, wos)
    fails = pd.concat([proc_fails, cmms_fails], ignore_index=True)

    proc.to_parquet(out / "process.parquet", index=False)
    sched.merge(batches, on=["batch_id", "control_strategy"], how="left").to_parquet(out / "batches.parquet", index=False)
    equip.to_parquet(out / "equipment.parquet", index=False)
    cals.to_parquet(out / "calibrations.parquet", index=False)
    wos.to_parquet(out / "work_orders.parquet", index=False)
    fails.to_parquet(out / "dq_failures.parquet", index=False)
    return {"process_rows": len(proc), "rows_failing_dq": int((~proc.dq_pass).sum()),
            "dq_failures_by_check": fails.groupby("check").size().to_dict()}
