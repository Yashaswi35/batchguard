"""Pipeline settings. Every threshold here is documented in docs/decision_log.md."""
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("BATCHGUARD_DATA", Path.home() / "Projects" / "batchguard_data"))
LAKE = DATA_DIR / "lake"

EQUIP_MAP = json.loads((REPO / "config" / "equipment_map.json").read_text())
VAR_TO_EQUIP_TYPE = EQUIP_MAP["variable_to_equipment_type"]
MONITORED_VARS = list(VAR_TO_EQUIP_TYPE)
CAL_INTERVAL_D = EQUIP_MAP["calibration_interval_days"]

# Golden-batch reference: batches released without deviation (IndPenSim batches 1-90)
REFERENCE_BATCHES = list(range(1, 91))
TIME_STEP_H = 0.2

Z_EVENT = 3.0             # a point is outside the golden-batch envelope
Z_SIGNIFICANT = 20.0      # an excursion large enough to call a process deviation
MIN_EVENT_H = 1.0         # ignore excursions shorter than 1 hour
MERGE_GAP_STEPS = 2       # bridge gaps of up to 2 in-range points inside one excursion
STD_FLOOR_FRAC = 0.10     # floor per-timestep std at 10% of the variable's global std
MIN_REF_BATCHES = 10      # need at least 10 reference batches at a timestep to judge it
RECENT_WO_LOOKBACK_D = 7  # closed work orders this close to the window are shown as context

# Physical plausibility rules for silver data quality checks (min, max). None = unbounded.
RANGE_RULES = {
    "ph": (0.0, 14.0),
    "temperature_k": (273.15, 373.15),
    "dissolved_o2_mg_l": (0.0, None),
    "aeration_rate_l_h": (0.0, None),
    "agitator_rpm": (0.0, None),
    "sugar_feed_l_h": (0.0, None),
    "acid_flow_l_h": (0.0, None),
    "base_flow_l_h": (0.0, None),
    "cooling_water_l_h": (0.0, None),
    "heating_water_l_h": (0.0, None),
    "substrate_g_l": (0.0, None),
    "co2_offgas_pct": (0.0, 100.0),
    "air_head_pressure_bar": (0.0, None),
    "vessel_volume_l": (0.0, None),
}
REQUIRED_PROCESS_COLS = ["batch_id", "time_h", "ph", "temperature_k"]

# Ground-truth labels never move past bronze, so no downstream layer or agent can see them.
LABEL_COLS = ["fault_ref", "fault_flag", "raman_flag", "batch_ref", "control_ref"]
