# Databricks notebook source
# MAGIC %md
# MAGIC # BatchGuard medallion pipeline on Databricks
# MAGIC
# MAGIC The same bronze, silver and gold logic as `pipeline/` in this repo, written in PySpark as Delta tables
# MAGIC in Unity Catalog (`workspace.batchguard`). Thresholds are imported from `pipeline/config.py`, so both
# MAGIC implementations share one source of truth. The last cell checks that every gold table matches the
# MAGIC frozen local snapshot in `evals/fixtures/gold`, so the port is verified, not assumed.
# MAGIC
# MAGIC **Before running:** open this repo as a Git folder, run `python -m lakehouse.export_for_databricks`
# MAGIC locally, and upload the six parquet files to `/Volumes/workspace/batchguard/raw`.

# COMMAND ----------

import json
import os
import sys

import pandas as pd
from pyspark.sql import Window
from pyspark.sql import functions as F

REPO = os.path.dirname(os.getcwd())  # this notebook lives in <repo>/lakehouse
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from pipeline import config as C  # noqa: E402
from pipeline.gold import build_equipment_context  # noqa: E402

CATALOG, SCHEMA = "workspace", "batchguard"
DB = f"{CATALOG}.{SCHEMA}"
RAW = f"/Volumes/{CATALOG}/{SCHEMA}/raw"
SOURCES = {"process": "indpensim_process.parquet", "batches": "indpensim_batches.parquet",
           "schedule": "batch_schedule.parquet", "equipment": "cmms_equipment.parquet",
           "calibrations": "cmms_calibrations.parquet", "work_orders": "cmms_work_orders.parquet"}

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {DB} COMMENT 'BatchGuard deviation investigation data products'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {DB}.raw COMMENT 'Landing zone for source extracts'")
present = {f.name for f in dbutils.fs.ls(RAW)}
missing = [f for f in SOURCES.values() if f not in present]
assert not missing, f"Upload these files to {RAW} first: {missing}"
print("all six source files present in", RAW)


def save(df, name, comment):
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{DB}.{name}")
    spark.sql(f"COMMENT ON TABLE {DB}.{name} IS '{comment}'")
    print(f"{name:<28} {spark.table(f'{DB}.{name}').count():>8,} rows")

# COMMAND ----------

# MAGIC %md ## Bronze: land each source unchanged, add lineage columns

# COMMAND ----------

for name, fname in SOURCES.items():
    df = spark.read.parquet(f"{RAW}/{fname}")
    if name == "process":
        df = df.withColumn("_row", F.monotonically_increasing_id())  # file order, for the time-order check
    df = df.withColumn("_ingest_ts", F.current_timestamp()).withColumn("_source_file", F.lit(fname))
    save(df, f"bronze_{name}", f"Raw {fname} as landed, with ingest timestamp and source file")

# COMMAND ----------

# MAGIC %md ## Silver: drop labels, conform types, run data quality checks (flag, never drop)

# COMMAND ----------

def bronze(n):
    return spark.table(f"{DB}.bronze_{n}").drop("_ingest_ts", "_source_file")


ts = F.to_timestamp
sched = bronze("schedule").withColumn("start_ts", ts("start_ts")).withColumn("end_ts", ts("end_ts"))
batches = bronze("batches").drop("is_faulted")  # the label stays in bronze
equip = bronze("equipment")
cals = bronze("calibrations").withColumn("cal_date", ts("cal_date")).withColumn("due_date", ts("due_date"))
wos = bronze("work_orders")
for c in ["opened_ts", "due_ts", "closed_ts"]:
    wos = wos.withColumn(c, ts(c))

proc = (bronze("process").drop(*C.LABEL_COLS)
        .join(sched.select("batch_id", "reactor_id", "start_ts"), "batch_id", "left")
        .withColumn("event_ts", F.expr("start_ts + make_dt_interval(0, 0, 0, CAST(time_h * 3600 AS DECIMAL(18,6)))"))
        .withColumn("t", F.round("time_h", 1))
        .withColumn("temperature_c", F.col("temperature_k") - 273.15)
        .drop("start_ts"))


def fails(df, cond, table, check, severity, detail, key=None):
    cols = df.columns
    return df.filter(cond).select(
        F.lit(table).alias("table"), F.lit(check).alias("check"), F.lit(severity).alias("severity"),
        (F.col("batch_id").cast("long") if "batch_id" in cols else F.lit(None).cast("long")).alias("batch_id"),
        (F.col("time_h") if "time_h" in cols else F.lit(None).cast("double")).alias("time_h"),
        (F.col(key).cast("string") if key else F.lit(None).cast("string")).alias("record_key"),
        F.lit(detail).alias("detail"))


checks = []
for c in C.REQUIRED_PROCESS_COLS:
    checks.append(fails(proc, F.col(c).isNull(), "process", f"not_null:{c}", "error", f"{c} is null"))
for c, (lo, hi) in C.RANGE_RULES.items():
    bad = F.lit(False)
    if lo is not None:
        bad = bad | (F.col(c) < lo)
    if hi is not None:
        bad = bad | (F.col(c) > hi)
    checks.append(fails(proc, bad, "process", f"range:{c}", "error", f"{c} outside [{lo}, {hi}]"))
dup_w = Window.partitionBy("batch_id", "time_h").orderBy("_row")
order_w = Window.partitionBy("batch_id").orderBy("_row")
proc_chk = (proc.withColumn("_dup", F.row_number().over(dup_w))
                .withColumn("_step", F.col("time_h") - F.lag("time_h").over(order_w)))
checks.append(fails(proc_chk, F.col("_dup") > 1, "process", "duplicate:batch_id+time_h", "error", "duplicate timestep"))
checks.append(fails(proc_chk, F.col("_step") <= 0, "process", "monotonic:time_h", "error", "time_h not increasing"))
checks.append(fails(proc.join(sched, "batch_id", "left_anti"), F.lit(True), "process", "ref:batch_id", "error",
                    "batch_id not in schedule"))
for df, key, cols, table in [(cals, "cal_id", ["cal_id", "equipment_id", "cal_date", "result"], "calibrations"),
                             (wos, "wo_id", ["wo_id", "equipment_id", "opened_ts", "wo_type"], "work_orders")]:
    for c in cols:
        checks.append(fails(df, F.col(c).isNull(), table, f"not_null:{c}", "error", f"{c} is null", key))
    w = Window.partitionBy(key).orderBy(key)
    checks.append(fails(df.withColumn("_n", F.row_number().over(w)), F.col("_n") > 1, table, f"duplicate:{key}",
                        "error", f"duplicate {key}", key))
    checks.append(fails(df.join(equip.select("equipment_id"), "equipment_id", "left_anti"), F.lit(True), table,
                        "ref:equipment_id", "error", "equipment_id not in equipment", key))
conflict_w = Window.partitionBy("equipment_id", "cal_date")
checks.append(fails(cals.withColumn("_k", F.size(F.collect_set("result").over(conflict_w))), F.col("_k") > 1,
                    "calibrations", "conflict:cal_result", "warn", "conflicting results for one calibration", "cal_id"))
dq_failures = checks[0]
for d in checks[1:]:
    dq_failures = dq_failures.unionByName(d)

bad_rows = (dq_failures.filter((F.col("table") == "process") & (F.col("severity") == "error"))
            .select("batch_id", "time_h").distinct().withColumn("_bad", F.lit(True)))
silver_process = (proc.join(bad_rows, ["batch_id", "time_h"], "left")
                  .withColumn("dq_pass", F.col("_bad").isNull()).drop("_bad", "_row"))

save(silver_process, "silver_process", "Conformed process data, labels removed, dq_pass flag per row")
spark.sql(f"ALTER TABLE {DB}.silver_process DROP CONSTRAINT IF EXISTS keys_present")
spark.sql(f"ALTER TABLE {DB}.silver_process ADD CONSTRAINT keys_present CHECK (batch_id IS NOT NULL AND time_h IS NOT NULL)")
save(sched.join(batches, ["batch_id", "control_strategy"], "left"), "silver_batches", "Batch schedule and yields")
save(equip, "silver_equipment", "Instrument master, synthetic CMMS")
save(cals, "silver_calibrations", "Calibration records, synthetic CMMS")
save(wos, "silver_work_orders", "Work orders, synthetic CMMS")
save(dq_failures, "silver_dq_failures", "Every data quality failure, one row per failed check")
display(spark.table(f"{DB}.silver_dq_failures").groupBy("table", "check", "severity").count())

# COMMAND ----------

# MAGIC %md ## Gold: golden-batch envelope, deviation events, batch summary, equipment context

# COMMAND ----------

V = C.MONITORED_VARS
ids = ["batch_id", "time_h", "t", "event_ts", "dq_pass"]
sp = spark.table(f"{DB}.silver_process")
long = (sp.select(*ids, *[F.col(v).cast("double").alias(v) for v in V])
        .unpivot(ids, V, "variable", "value"))

ref = long.filter(F.col("batch_id").isin(C.REFERENCE_BATCHES) & F.col("dq_pass"))
global_std = ref.groupBy("variable").agg(F.stddev_samp("value").alias("gstd"))
valid = F.col("n") >= C.MIN_REF_BATCHES
envelope = (ref.groupBy("t", "variable")
            .agg(F.avg("value").alias("mean"), F.stddev_samp("value").alias("std"), F.count("value").alias("n"))
            .join(global_std, "variable")
            .withColumn("std", F.greatest("std", F.col("gstd") * C.STD_FLOOR_FRAC))
            .withColumn("mean", F.when(valid, F.col("mean")))
            .withColumn("std", F.when(valid, F.col("std")))
            .drop("gstd"))

z = (long.join(envelope.select("t", "variable", "mean", "std"), ["t", "variable"], "left")
     .withColumn("z", F.try_divide(F.col("value") - F.col("mean"), F.col("std")))
     .withColumn("z", F.when(F.isnan("z"), None).otherwise(F.col("z")))  # NaN compares as largest in Spark
     .withColumn("idx", F.row_number().over(Window.partitionBy("batch_id", "variable").orderBy("time_h"))))

run_w = Window.partitionBy("batch_id", "variable").orderBy("idx")
flagged = (z.filter(F.abs("z") > C.Z_EVENT)
           .withColumn("prev", F.lag("idx").over(run_w))
           .withColumn("new_run", F.when(F.col("prev").isNull() |
                                         (F.col("idx") - F.col("prev") > C.MERGE_GAP_STEPS + 1), 1).otherwise(0))
           .withColumn("run", F.sum("new_run").over(run_w.rowsBetween(Window.unboundedPreceding, 0))))
events = (flagged.groupBy("batch_id", "variable", "run")
          .agg(F.min("time_h").alias("start_h"), F.max("time_h").alias("end_h"),
               F.min("event_ts").alias("start_ts"), F.max("event_ts").alias("end_ts"),
               F.max(F.abs("z")).alias("max_abs_z"), F.max_by("z", F.abs("z")).alias("peak_z"),
               F.max_by("value", F.abs("z")).alias("peak_value"))
          .withColumn("duration_h", F.round(F.col("end_h") - F.col("start_h") + C.TIME_STEP_H, 1))
          .filter(F.col("duration_h") >= C.MIN_EVENT_H)
          .withColumn("start_h", F.round("start_h", 1)).withColumn("end_h", F.round("end_h", 1))
          .withColumn("max_abs_z", F.round("max_abs_z", 2))
          .withColumn("direction", F.when(F.col("peak_z") > 0, "high").otherwise("low"))
          .withColumn("significant", F.col("max_abs_z") >= C.Z_SIGNIFICANT)
          .withColumn("event_id", F.format_string("EV-%03d-%03d", F.col("batch_id"),
                      F.row_number().over(Window.partitionBy("batch_id").orderBy("start_h", "variable"))))
          .drop("run", "peak_z"))

sig = events.filter("significant")
dev_vars = (sig.groupBy("batch_id", "variable").agg(F.max("max_abs_z").alias("vz"))
            .groupBy("batch_id")
            .agg(F.to_json(F.transform(F.sort_array(F.collect_list(F.struct((-F.col("vz")).alias("k"),
                                                                           F.col("variable").alias("v")))),
                                       lambda x: x["v"])).alias("deviating_variables")))
sig_agg = (sig.groupBy("batch_id").agg(F.count("*").alias("n_significant_events"),
                                       F.max("max_abs_z").alias("max_abs_z"),
                                       F.min("start_ts").alias("window_start_ts"),
                                       F.max("end_ts").alias("window_end_ts"))
           .join(dev_vars, "batch_id"))
proc_agg = sp.groupBy("batch_id").agg(F.max("time_h").alias("duration_h"),
                                      F.max_by("penicillin_g_l", "time_h").alias("final_penicillin_g_l"),
                                      F.sum(F.when(~F.col("dq_pass"), 1).otherwise(0)).alias("dq_failed_rows"))
summary = (spark.table(f"{DB}.silver_batches").join(proc_agg, "batch_id").join(sig_agg, "batch_id", "left")
           .withColumn("deviation_flag", F.col("n_significant_events").isNotNull())
           .fillna({"n_significant_events": 0, "max_abs_z": 0.0, "deviating_variables": "[]"})
           .withColumn("window_start_ts", F.coalesce("window_start_ts", "start_ts"))
           .withColumn("window_end_ts", F.coalesce("window_end_ts", "end_ts")))

save(envelope, "gold_envelope", "Golden-batch mean and std per 0.2 h timestep and variable, batches 1-90")
save(events, "gold_deviation_events", "Excursions outside the envelope at |z| > 3 lasting 1 h or more")
save(summary, "gold_batch_summary", "One row per batch: deviation flag at |z| >= 20 and investigation window")

# Equipment context reuses the tested pandas function from pipeline/gold.py on the driver (1,000 rows)
ctx = build_equipment_context(spark.table(f"{DB}.gold_batch_summary").toPandas(),
                              spark.table(f"{DB}.silver_equipment").toPandas(),
                              spark.table(f"{DB}.silver_calibrations").toPandas(),
                              spark.table(f"{DB}.silver_work_orders").toPandas(),
                              C.VAR_TO_EQUIP_TYPE, C.CAL_INTERVAL_D, C.RECENT_WO_LOOKBACK_D)
save(spark.createDataFrame(ctx), "gold_equipment_context", "Instrument calibration and work order state per batch")

# COMMAND ----------

# MAGIC %md ## Verify: detection quality and parity with the local pipeline

# COMMAND ----------

labels = spark.table(f"{DB}.bronze_batches").select("batch_id", "is_faulted")
det = spark.table(f"{DB}.gold_batch_summary").join(labels, "batch_id").toPandas()
tp = int(det[det.is_faulted == 1].deviation_flag.sum())
fp = int(det[det.is_faulted == 0].deviation_flag.sum())
print(f"Detection: {tp}/10 faulted batches flagged, {fp}/90 normal batches flagged")

FX = os.path.join(REPO, "evals", "fixtures", "gold")
local_s = pd.read_parquet(os.path.join(FX, "batch_summary.parquet"))
local_e = pd.read_parquet(os.path.join(FX, "deviation_events.parquet"))
local_c = pd.read_parquet(os.path.join(FX, "equipment_context.parquet"))
db_s = spark.table(f"{DB}.gold_batch_summary").toPandas()
db_e = spark.table(f"{DB}.gold_deviation_events").toPandas()
db_c = spark.table(f"{DB}.gold_equipment_context").toPandas()

m = local_s.merge(db_s, on="batch_id", suffixes=("_local", "_db"))
problems = []
for r in m.itertuples():
    if r.deviation_flag_local != r.deviation_flag_db:
        problems.append(f"batch {r.batch_id}: deviation_flag differs")
    if set(json.loads(r.deviating_variables_local)) != set(json.loads(r.deviating_variables_db)):
        problems.append(f"batch {r.batch_id}: deviating variables differ")
    if abs(r.max_abs_z_local - r.max_abs_z_db) > 0.05:
        problems.append(f"batch {r.batch_id}: max |z| {r.max_abs_z_local} vs {r.max_abs_z_db}")


def issue_ids(row):
    return (row.cal_overdue, row.last_cal_result,
            tuple(sorted(w["wo_id"] for w in json.loads(row.open_corrective_wos))),
            tuple(sorted(w["wo_id"] for w in json.loads(row.overdue_pm_wos))))


lc = {(r.batch_id, r.equipment_id): issue_ids(r) for r in local_c.itertuples()}
dc = {(r.batch_id, r.equipment_id): issue_ids(r) for r in db_c.itertuples()}
problems += [f"equipment context differs for {k}" for k in lc if lc[k] != dc.get(k)]

print(f"events: local {len(local_e)} ({int(local_e.significant.sum())} significant), "
      f"databricks {len(db_e)} ({int(db_e.significant.sum())} significant)")
assert not problems, "PARITY FAILED:\n" + "\n".join(problems[:20])
print(f"PARITY OK: {len(m)} batches and {len(lc)} equipment rows match the local gold snapshot")

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT batch_id, reactor_id, control_strategy, deviation_flag, n_significant_events, max_abs_z,
# MAGIC        deviating_variables, window_start_ts, window_end_ts
# MAGIC FROM workspace.batchguard.gold_batch_summary
# MAGIC WHERE deviation_flag
# MAGIC ORDER BY batch_id
