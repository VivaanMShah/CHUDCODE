"""Paths and tunable constants. Placeholders are labeled so they show up in meta.assumptions."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "_cache"
LOG_DIR = ROOT / "logs"

FILES = {
    "arcos": DATA_DIR / "Data1" / "arcos_prod_damage_09_28_Clip.shp",
    "customers": DATA_DIR / "Data1" / "Customer_Clip.shp",
    "poles": DATA_DIR / "Data1" / "Pole_Clip.shp",
    "primary": DATA_DIR / "Data1" / "Primary_ExportFeatures.shp",
    "fuses": DATA_DIR / "Data1" / "Fuse_Bank_Clip.shp",
    "reclosers": DATA_DIR / "Data2" / "Recloser_Bank_Clip.shp",
    "switches": DATA_DIR / "Data2" / "Switch_Clip.shp",
    "transformers": DATA_DIR / "Data2" / "Transformer_Clip.shp",
    "hospitals": DATA_DIR / "Data1" / "Hospitals_Medic_Pairwis_Clip.shp",
    "fire": DATA_DIR / "Data1" / "Fire_Stations_E_Pairwis_Clip.shp",
    "ambulance": DATA_DIR / "Data1" / "Ambulance_Servi_Pairwis_Clip.shp",
    "topology": DATA_DIR / "matched_upstream" / "matched_upstream.txt",
    # Not in the data package yet. Restoration uses labeled placeholders until they arrive.
    "storms_labor": DATA_DIR / "storms_labor_hours.xlsx",
    "indirect_labor": DATA_DIR / "indirect_labor.xlsx",
}

# Projected CRS for distance math (UTM 17N covers the Roanoke service area).
METRIC_CRS = "EPSG:32617"

# Locations: one per circuit with at least this many customers.
MIN_CUSTOMERS_PER_LOCATION = 50

# Critical facility "nearby" = within this distance of the circuit's primary lines.
CRITICAL_FACILITY_RADIUS_M = 800

# Monte Carlo (fixed so the demo is repeatable)
MC_SAMPLES = 2000
MC_SEED = 42

# Placeholder crew-hours per damage item, used until the STORMS labor-hour file is provided.
PLACEHOLDER_LABOR_HOURS = {
    "pole": 6.0,
    "primary_wire": 2.5,
    "secondary_wire": 1.5,
    "transformer": 3.0,
    "fuse_switch": 1.0,
    "recloser": 3.0,
    "other_equipment": 2.0,
}
PLACEHOLDER_TRAVEL_MULT = 1.25
PLACEHOLDER_NONPRODUCTIVE_MULT = 1.15
BLOCKED_ROADS_TRAVEL_MULT = 1.5
CREW_AVAILABILITY = 0.85  # chance each assigned crew is actually working at a given time

# Storm severity scale. The largest storm in ARCOS history (Feb 2025) is treated as severity 4.
REFERENCE_SEVERITY = 4
SEVERITY_LOGIT_STEP = 1.0  # risk log-odds change per severity level
SEVERITY_DAMAGE_MULT = {1: 0.05, 2: 0.2, 3: 0.5, 4: 1.0, 5: 1.8}

RISK_BANDS = [(0.35, "low"), (0.65, "medium"), (1.01, "high")]

# Backup battery life we assume for home medical equipment when deciding escalation.
MEDICAL_BACKUP_HOURS = 8
