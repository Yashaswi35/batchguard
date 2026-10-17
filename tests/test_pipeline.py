"""Unit tests for pipeline logic on small hand-built frames. No data files needed."""
import json

import numpy as np
import pandas as pd
import pytest

from pipeline import dq
from pipeline.gold import build_envelope, build_equipment_context, find_events, zscores

T0 = pd.Timestamp("2026-01-01")


def make_proc(n=40, batch=1, values=None):
    t = np.round(np.arange(1, n + 1) * 0.2, 1)
    df = pd.DataFrame({"batch_id": batch, "time_h": t, "t": t,
                       "event_ts": T0 + pd.to_timedelta(t, unit="h"), "dq_pass": True})
    df["x"] = values if values is not None else 0.0
    return df


# ---------------- data quality ----------------
def test_range_check_flags_impossible_ph():
    df = pd.DataFrame({"batch_id": [1, 1, 1], "time_h": [0.2, 0.4, 0.6], "ph": [6.5, 15.2, -1.0]})
    fails = dq.check_range(df, {"ph": (0.0, 14.0)}, "process")
    assert sorted(fails.time_h) == [0.4, 0.6]
    assert set(fails.severity) == {"error"}


def test_duplicate_check_keeps_first_flags_rest():
    df = pd.DataFrame({"batch_id": [1, 1, 1], "time_h": [0.2, 0.2, 0.4]})
    fails = dq.check_duplicates(df, ["batch_id", "time_h"], "process")
    assert len(fails) == 1 and fails.time_h.iloc[0] == 0.2


def test_monotonic_time_detects_backwards_step():
    df = pd.DataFrame({"batch_id": [1, 1, 1, 2], "time_h": [0.2, 0.4, 0.3, 0.2]})
    fails = dq.check_monotonic_time(df, "process")
    assert list(fails.time_h) == [0.3]


def test_conflicting_calibration_results_are_warnings():
    cals = pd.DataFrame({"cal_id": ["C1", "C2", "C3"], "equipment_id": ["PH-1", "PH-1", "PH-1"],
                         "cal_date": [T0, T0, T0 + pd.Timedelta(days=60)], "result": ["PASS", "FAIL", "PASS"]})
    fails = dq.check_conflicting_results(cals)
    assert sorted(fails.record_key) == ["C1", "C2"]
    assert set(fails.severity) == {"warn"}


# ---------------- envelope and events ----------------
def test_std_floor_prevents_exploding_zscores():
    ref = pd.concat([make_proc(batch=b) for b in range(1, 12)])  # x is constant zero, std = 0
    ref.loc[ref.batch_id == 1, "x"] = 1.0                          # one batch differs, global std > 0
    mean, std = build_envelope(ref, ["x"], list(range(1, 12)), std_floor_frac=0.10, min_ref=10)
    assert (std["x"] > 0).all()


def test_envelope_requires_minimum_reference_batches():
    ref = pd.concat([make_proc(batch=b) for b in range(1, 6)])
    mean, std = build_envelope(ref, ["x"], list(range(1, 6)), std_floor_frac=0.1, min_ref=10)
    assert mean["x"].isna().all()


def test_find_events_merges_small_gaps_and_drops_short_runs():
    z = np.zeros(40)
    z[2:5] = 8          # 3 points = 0.6 h, shorter than 1 h, dropped
    z[10:16] = 12       # 6 points
    z[17:22] = 12       # 1-point gap, merged with the run above
    z[30:36] = -20      # separate low excursion
    proc = make_proc(values=0)
    zdf = pd.DataFrame({"x": z}, index=proc.index)
    ev = find_events(proc, zdf, z_event=3, min_event_h=1.0, merge_gap_steps=2, step_h=0.2)
    assert len(ev) == 2
    first, second = ev.iloc[0], ev.iloc[1]
    assert (first.start_h, first.end_h, first.direction) == (2.2, 4.4, "high")
    assert second.direction == "low" and second.max_abs_z == 20


def test_zscores_use_timestep_envelope():
    proc = make_proc(n=3, values=[1.0, 2.0, 3.0])
    mean = pd.DataFrame({"x": [0.0, 0.0, 0.0]}, index=pd.Index([0.2, 0.4, 0.6], name="t"))
    std = pd.DataFrame({"x": [1.0, 2.0, 3.0]}, index=mean.index)
    z = zscores(proc, mean, std, ["x"])
    assert list(z["x"]) == [1.0, 1.0, 1.0]


# ---------------- equipment context ----------------
@pytest.fixture
def cmms():
    equip = pd.DataFrame([dict(equipment_id="PH-1", reactor_id="R1", equipment_type="PH",
                               description="pH probe", criticality="high", calibrated=True),
                          dict(equipment_id="BP-1", reactor_id="R1", equipment_type="BP",
                               description="Base pump", criticality="medium", calibrated=False)])
    summary = pd.DataFrame([dict(batch_id=1, reactor_id="R1", deviating_variables=json.dumps(["ph"]),
                                 window_start_ts=T0 + pd.Timedelta(days=100),
                                 window_end_ts=T0 + pd.Timedelta(days=101))])
    var_map = {"ph": ["PH", "BP"], "base_flow_l_h": ["BP"]}
    return equip, summary, var_map


def test_overdue_calibration_detected(cmms):
    equip, summary, var_map = cmms
    cals = pd.DataFrame([dict(cal_id="C1", equipment_id="PH-1", cal_date=T0, result="PASS", as_found_note="ok")])
    wos = pd.DataFrame(columns=["wo_id", "equipment_id", "wo_type", "opened_ts", "due_ts", "closed_ts",
                                "description", "notes"])
    ctx = build_equipment_context(summary, equip, cals, wos, var_map, cal_interval_d=90, lookback_d=7)
    ph = ctx[ctx.equipment_id == "PH-1"].iloc[0]
    assert ph.cal_overdue and ph.cal_days_overdue == 10 and ph.linked_to_deviation


def test_conflicting_calibration_reported_as_conflict(cmms):
    equip, summary, var_map = cmms
    d = T0 + pd.Timedelta(days=95)
    cals = pd.DataFrame([dict(cal_id="C1", equipment_id="PH-1", cal_date=d, result="PASS", as_found_note="ok"),
                         dict(cal_id="C2", equipment_id="PH-1", cal_date=d, result="FAIL", as_found_note="4%")])
    wos = pd.DataFrame(columns=["wo_id", "equipment_id", "wo_type", "opened_ts", "due_ts", "closed_ts",
                                "description", "notes"])
    ctx = build_equipment_context(summary, equip, cals, wos, var_map, cal_interval_d=90, lookback_d=7)
    ph = ctx[ctx.equipment_id == "PH-1"].iloc[0]
    assert ph.last_cal_result == "CONFLICT" and json.loads(ph.last_cal_ids) == ["C1", "C2"]


def test_open_corrective_wo_only_counts_if_open_during_window(cmms):
    equip, summary, var_map = cmms
    cals = pd.DataFrame(columns=["cal_id", "equipment_id", "cal_date", "result", "as_found_note"])
    day = lambda n: T0 + pd.Timedelta(days=n)
    wos = pd.DataFrame([
        dict(wo_id="W1", equipment_id="BP-1", wo_type="CORRECTIVE", opened_ts=day(98), due_ts=day(103),
             closed_ts=day(102), description="pump underdelivering", notes=""),
        dict(wo_id="W2", equipment_id="BP-1", wo_type="CORRECTIVE", opened_ts=day(80), due_ts=day(85),
             closed_ts=day(84), description="closed long before", notes=""),
    ])
    ctx = build_equipment_context(summary, equip, cals, wos, var_map, cal_interval_d=90, lookback_d=7)
    bp = ctx[ctx.equipment_id == "BP-1"].iloc[0]
    assert [w["wo_id"] for w in json.loads(bp.open_corrective_wos)] == ["W1"]
