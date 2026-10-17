"""Gold: the data products the agent reads.

- envelope:           golden-batch mean and std per timestep and variable
- deviation_events:   contiguous excursions outside the envelope, one row per batch x variable x run
- batch_summary:      one row per batch with its deviation flag and investigation window
- equipment_context:  maintenance and calibration state of every instrument during that window
"""
import json

import numpy as np
import pandas as pd

from . import config as C


def build_envelope(proc, variables, reference_batches, std_floor_frac, min_ref):
    ref = proc[proc.batch_id.isin(reference_batches) & proc.dq_pass]
    g = ref.groupby("t")[variables]
    mean, std, n = g.mean(), g.std(), g.count()
    floor = ref[variables].std() * std_floor_frac
    std = std.clip(lower=floor, axis=1)
    valid = n >= min_ref
    return mean.where(valid), std.where(valid)


def zscores(proc, mean, std, variables):
    m = mean.reindex(proc["t"]).to_numpy()
    s = std.reindex(proc["t"]).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):  # NaN where envelope has < MIN_REF batches
        z = (proc[variables].to_numpy() - m) / s
    return pd.DataFrame(z, columns=variables, index=proc.index)


def find_events(proc, z, z_event, min_event_h, merge_gap_steps, step_h):
    """Group out-of-envelope points into contiguous excursions per batch and variable."""
    rows = []
    for b, grp in proc.groupby("batch_id", sort=True):
        zg = z.loc[grp.index]
        t = grp["time_h"].to_numpy()
        ts = grp["event_ts"].to_numpy()
        for v in z.columns:
            zv = zg[v].to_numpy()
            idx = np.flatnonzero(np.nan_to_num(np.abs(zv)) > z_event)
            if idx.size == 0:
                continue
            runs = np.split(idx, np.flatnonzero(np.diff(idx) > merge_gap_steps + 1) + 1)
            for run in runs:
                dur = round(float(t[run[-1]] - t[run[0]] + step_h), 1)
                if dur < min_event_h:
                    continue
                k = run[np.argmax(np.abs(zv[run]))]
                rows.append(dict(
                    batch_id=int(b), variable=v,
                    start_h=round(float(t[run[0]]), 1), end_h=round(float(t[run[-1]]), 1),
                    duration_h=dur, start_ts=ts[run[0]], end_ts=ts[run[-1]],
                    max_abs_z=round(float(abs(zv[k])), 2),
                    direction="high" if zv[k] > 0 else "low",
                    peak_value=float(grp[v].to_numpy()[k]),
                ))
    ev = pd.DataFrame(rows)
    if ev.empty:
        return ev
    ev = ev.sort_values(["batch_id", "start_h", "variable"]).reset_index(drop=True)
    ev.insert(0, "event_id", [f"EV-{b:03d}-{i:03d}" for b, i in
                              zip(ev.batch_id, ev.groupby("batch_id").cumcount() + 1)])
    return ev


def build_batch_summary(batches, proc, events, z_significant):
    agg = proc.groupby("batch_id").agg(duration_h=("time_h", "max"),
                                       final_penicillin_g_l=("penicillin_g_l", "last"),
                                       dq_failed_rows=("dq_pass", lambda s: int((~s).sum())))
    sig = events[events.max_abs_z >= z_significant] if not events.empty else events
    rows = []
    for b in batches.batch_id:
        e = sig[sig.batch_id == b] if not sig.empty else sig
        if len(e):
            top = e.groupby("variable").max_abs_z.max().sort_values(ascending=False)
            rows.append(dict(batch_id=b, deviation_flag=True, n_significant_events=len(e),
                             max_abs_z=float(top.iloc[0]), deviating_variables=json.dumps(list(top.index)),
                             window_start_ts=e.start_ts.min(), window_end_ts=e.end_ts.max()))
        else:
            rows.append(dict(batch_id=b, deviation_flag=False, n_significant_events=0, max_abs_z=0.0,
                             deviating_variables="[]", window_start_ts=pd.NaT, window_end_ts=pd.NaT))
    s = batches.merge(agg, on="batch_id").merge(pd.DataFrame(rows), on="batch_id")
    s["window_start_ts"] = s.window_start_ts.fillna(s.start_ts)
    s["window_end_ts"] = s.window_end_ts.fillna(s.end_ts)
    return s


def build_equipment_context(summary, equip, cals, wos, var_map, cal_interval_d, lookback_d):
    """Instrument state at investigation time. Record IDs are kept so the agent can cite them."""
    type_to_vars = {}
    for var, types in var_map.items():
        for ty in types:
            type_to_vars.setdefault(ty, []).append(var)
    rows = []
    for s in summary.itertuples():
        ws, we = s.window_start_ts, s.window_end_ts
        deviating = set(json.loads(s.deviating_variables))
        for e in equip[equip.reactor_id == s.reactor_id].itertuples():
            linked = type_to_vars.get(e.equipment_type, [])
            c = cals[(cals.equipment_id == e.equipment_id) & (cals.cal_date <= ws)]
            if e.calibrated and len(c):
                last_date = c.cal_date.max()
                last = c[c.cal_date == last_date]
                conflict = last.result.nunique() > 1
                due = last_date + pd.Timedelta(days=cal_interval_d)
                cal = dict(last_cal_ids=json.dumps(sorted(last.cal_id)), last_cal_date=last_date,
                           last_cal_result="CONFLICT" if conflict else last.result.iloc[0],
                           last_cal_note=" | ".join(last.as_found_note), cal_due_date=due,
                           cal_overdue=bool(due < ws), cal_days_overdue=max(0, (ws - due).days))
            else:
                cal = dict(last_cal_ids="[]", last_cal_date=pd.NaT, last_cal_result=None, last_cal_note=None,
                           cal_due_date=pd.NaT, cal_overdue=False, cal_days_overdue=0)
            w = wos[wos.equipment_id == e.equipment_id]
            open_corr = w[(w.wo_type == "CORRECTIVE") & (w.opened_ts <= we)
                          & (w.closed_ts.isna() | (w.closed_ts >= ws))]
            overdue_pm = w[(w.wo_type == "PREVENTIVE") & (w.due_ts < ws)
                           & (w.closed_ts.isna() | (w.closed_ts > ws))]
            recent = w[(w.closed_ts >= ws - pd.Timedelta(days=lookback_d)) & (w.closed_ts < ws)]
            rows.append(dict(
                batch_id=s.batch_id, reactor_id=s.reactor_id, equipment_id=e.equipment_id,
                equipment_type=e.equipment_type, description=e.description, criticality=e.criticality,
                linked_variables=json.dumps(linked),
                linked_to_deviation=bool(deviating & set(linked)),
                **cal,
                open_corrective_wos=json.dumps([dict(wo_id=r.wo_id, opened_ts=str(r.opened_ts),
                                                     description=r.description, notes=r.notes)
                                                for r in open_corr.itertuples()]),
                overdue_pm_wos=json.dumps([dict(wo_id=r.wo_id, due_ts=str(r.due_ts), description=r.description,
                                                notes=r.notes) for r in overdue_pm.itertuples()]),
                recent_closed_wos=json.dumps([dict(wo_id=r.wo_id, closed_ts=str(r.closed_ts),
                                                   description=r.description, notes=r.notes)
                                              for r in recent.itertuples()]),
            ))
    return pd.DataFrame(rows)


def run(lake):
    src, out = lake / "silver", lake / "gold"
    out.mkdir(parents=True, exist_ok=True)
    proc = pd.read_parquet(src / "process.parquet")
    batches = pd.read_parquet(src / "batches.parquet")
    equip = pd.read_parquet(src / "equipment.parquet")
    cals = pd.read_parquet(src / "calibrations.parquet")
    wos = pd.read_parquet(src / "work_orders.parquet")

    V = C.MONITORED_VARS
    mean, std = build_envelope(proc, V, C.REFERENCE_BATCHES, C.STD_FLOOR_FRAC, C.MIN_REF_BATCHES)
    z = zscores(proc, mean, std, V)
    events = find_events(proc, z, C.Z_EVENT, C.MIN_EVENT_H, C.MERGE_GAP_STEPS, C.TIME_STEP_H)
    events["significant"] = events.max_abs_z >= C.Z_SIGNIFICANT
    summary = build_batch_summary(batches, proc, events, C.Z_SIGNIFICANT)
    context = build_equipment_context(summary, equip, cals, wos, C.VAR_TO_EQUIP_TYPE,
                                      C.CAL_INTERVAL_D, C.RECENT_WO_LOOKBACK_D)

    env = mean.add_suffix("__mean").join(std.add_suffix("__std")).reset_index()
    env.to_parquet(out / "envelope.parquet", index=False)
    events.to_parquet(out / "deviation_events.parquet", index=False)
    summary.to_parquet(out / "batch_summary.parquet", index=False)
    context.to_parquet(out / "equipment_context.parquet", index=False)
    return {"events": len(events), "significant_events": int(events.significant.sum()),
            "batches_flagged": int(summary.deviation_flag.sum()), "equipment_context_rows": len(context)}
