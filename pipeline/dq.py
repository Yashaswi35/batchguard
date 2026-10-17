"""Data quality checks. Each check returns a DataFrame of failures in one shared shape."""
import pandas as pd

FAIL_COLS = ["table", "check", "severity", "batch_id", "time_h", "record_key", "detail"]


def _failures(df, table, check, severity, detail, key_col=None):
    if df.empty:
        return pd.DataFrame(columns=FAIL_COLS)
    return pd.DataFrame({
        "table": table,
        "check": check,
        "severity": severity,
        "batch_id": df["batch_id"].values if "batch_id" in df else None,
        "time_h": df["time_h"].values if "time_h" in df else None,
        "record_key": df[key_col].astype(str).values if key_col else None,
        "detail": detail,
    })


def check_not_null(df, cols, table, key_col=None):
    out = []
    for c in cols:
        bad = df[df[c].isna()]
        out.append(_failures(bad, table, f"not_null:{c}", "error", f"{c} is null", key_col))
    return pd.concat(out, ignore_index=True)


def check_range(df, rules, table):
    out = []
    for c, (lo, hi) in rules.items():
        if c not in df:
            continue
        bad = pd.Series(False, index=df.index)
        if lo is not None:
            bad |= df[c] < lo
        if hi is not None:
            bad |= df[c] > hi
        out.append(_failures(df[bad], table, f"range:{c}", "error", f"{c} outside [{lo}, {hi}]"))
    return pd.concat(out, ignore_index=True)


def check_duplicates(df, keys, table, key_col=None):
    bad = df[df.duplicated(keys, keep="first")]
    return _failures(bad, table, "duplicate:" + "+".join(keys), "error", f"duplicate on {keys}", key_col)


def check_monotonic_time(df, table):
    step = df.groupby("batch_id")["time_h"].diff()
    bad = df[step <= 0]
    return _failures(bad, table, "monotonic:time_h", "error", "time_h not strictly increasing")


def check_referential(child, col, parent_values, table, key_col):
    bad = child[~child[col].isin(set(parent_values))]
    return _failures(bad, table, f"ref:{col}", "error", f"{col} not found in parent table", key_col)


def check_conflicting_results(cals, table="calibrations"):
    """Same instrument, same calibration date, different results. Kept, but flagged as a warning."""
    g = cals.groupby(["equipment_id", "cal_date"])["result"].transform("nunique")
    bad = cals[g > 1]
    return _failures(bad, table, "conflict:cal_result", "warn",
                     "conflicting results recorded for the same calibration event", "cal_id")
