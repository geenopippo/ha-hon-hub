"""Constants for hOn Hub. No Home Assistant imports here: the core modules are tested standalone."""
from __future__ import annotations

DOMAIN = "hon_hub"
MANUFACTURER_FALLBACK = "hOn"

CONF_MOBILE_ID = "mobile_id"

# seconds
POLL_PUSH = 180            # REST safety net while the real-time channel is up
POLL_NO_PUSH = 30          # REST polling while the real-time channel is down
SLOW_TTL = 1800            # maintenance counters and statistics
COMMANDS_TTL = 24 * 3600   # program definitions and appliance list
I18N_TTL = 7 * 86400       # program names downloaded from hOn
PENDING_TTL = 90           # settings just sent: the cloud reports them with some delay
STALE_GRACE = 900          # the last reading stays good this long while the cloud does not answer
STARTED_TTL = 120          # a start sent from here names the cycle until the cloud does, for this long at most

# appliance types
WASHING = ("WM", "WD", "TD")           # drum machines: same phase table
CYCLE_TYPES = ("WM", "WD", "TD", "DW")  # appliances that run programs with a remaining time
HOB = "IH"
NO_MAINTENANCE = ("IH", "OV", "HO", "REF", "WC")   # maintenance-cycle answers 400 for these

TYPE_NAMES = {"WD": "washer_dryer", "WM": "washing_machine", "TD": "tumble_dryer", "DW": "dishwasher",
              "IH": "induction_hob", "OV": "oven", "HO": "hood", "REF": "fridge", "WC": "wine_cellar"}

# machMode -> state
MACH_MODE = {0: "ready", 1: "ready", 2: "running", 3: "paused", 4: "scheduled", 5: "scheduled",
             6: "error", 7: "finished", 8: "test", 9: "stopping"}
STATES = ["ready", "running", "paused", "scheduled", "error", "finished", "test", "stopping", "offline"]
STATES_HOB = ["on", "off", "offline"]
ACTIVE_STATES = ("running", "paused", "scheduled")

# prPhase -> phase
PHASE_WASH = {1: "washing", 2: "washing", 3: "spin", 4: "rinse", 5: "rinse", 6: "rinse", 7: "drying", 8: "drying",
              9: "steam", 11: "spin", 12: "weighting", 13: "weighting", 14: "washing", 15: "washing", 16: "washing",
              17: "rinse", 18: "rinse", 19: "scheduled", 20: "tumbling", 24: "refresh", 25: "washing",
              26: "heating", 27: "washing"}
PHASE_DRYER = {1: "heat_stroke", 2: "drying", 3: "cooldown", 13: "cooldown", 14: "heat_stroke", 15: "heat_stroke",
               16: "cooldown", 18: "tumbling", 19: "drying", 20: "drying"}
PHASE_DISH = {1: "prewash", 2: "washing", 3: "rinse", 4: "drying", 5: "finished", 6: "hot_rinse"}
PHASES = sorted({*PHASE_WASH.values(), *PHASE_DRYER.values(), *PHASE_DISH.values()})

DRY_LEVELS = {0: "no_dry", 1: "iron_dry", 2: "no_dry_iron", 3: "cupboard_dry", 4: "extra_dry",
              11: "no_dry", 12: "iron_dry", 13: "cupboard_dry", 14: "ready_to_wear", 15: "extra_dry"}

# maintenance counters returned by commands/v1/maintenance-cycle
MAINTENANCE = ("filterCleaning", "drumCleaning", "drawerCleaning", "sprayArmsCleaning")
# counters the official app lets you reset by hand, with the service it uses:
# "commands" = POST commands/v1/resetCounter, "notification" = POST notification/v1/filter-cleaning/<mac>
RESETTABLE = {
    "DW": {"filterCleaning": "commands", "sprayArmsCleaning": "commands", "drawerCleaning": "commands"},
    "WD": {"filterCleaning": "notification", "drawerCleaning": "commands"},
    "WM": {"filterCleaning": "notification", "drawerCleaning": "commands"},
}

SIMPLE_COMMANDS = ("pauseProgram", "resumeProgram", "stopProgram")

# program/setting parameters with a translated name (translation key = snake case of the key)
KNOWN_PARAMS = {
    "temp": "temp", "spinSpeed": "spin_speed", "dryLevel": "dry_level", "dryTime": "dry_time",
    "dryOption": "dry_option", "dirtyLevel": "dirty_level", "steamLevel": "steam_level", "delayTime": "delay_time",
    "extraRinse1": "extra_rinse_1", "extraRinse2": "extra_rinse_2", "extraRinse3": "extra_rinse_3",
    "acquaplus": "acquaplus", "goodNight": "good_night", "prewash": "prewash", "hygiene": "hygiene",
    "anticrease": "anticrease", "autoDetergentStatus": "auto_detergent", "autoSoftenerStatus": "auto_softener",
    "waterHard": "water_hardness", "extraDry": "extra_dry", "openDoor": "open_door", "halfLoad": "half_load",
    "ecoExpress": "eco_express", "threeInOne": "three_in_one", "addDish": "add_dish", "tabStatus": "tab_status",
    "intensive": "intensive", "diverterLevel": "diverter_level", "lightStatus": "light",
    "buzzerDisabled": "buzzer_disabled", "lastCycleSavingStatus": "last_cycle_memo",
    "multiDosingLevelRA": "rinse_aid_level", "powerManagement": "power_management",
    "nightWashStatus": "night_wash", "energySavingStatus": "energy_saving", "sterilizationStatus": "sterilization",
    "rinseIterations": "rinse_iterations", "mainWashTime": "main_wash_time", "lockStatus": "key_lock",
    "zoneMap": "zone_map", "autoDose": "auto_dose",
}
# parameters that are a choice among named levels: always a list, never a number or an on/off switch
LEVELS = ("dirtyLevel", "steamLevel", "diverterLevel", "dryLevel", "temp", "spinSpeed")
# order of the program options on the device page
PARAM_ORDER = ["temp", "spinSpeed", "dirtyLevel", "dryOption", "dryLevel", "dryTime", "steamLevel", "prewash", "hygiene",
               "intensive", "extraRinse1", "extraRinse2", "extraRinse3", "acquaplus", "anticrease", "goodNight",
               "halfLoad", "ecoExpress", "extraDry", "openDoor", "threeInOne", "tabStatus", "addDish", "diverterLevel",
               "autoDetergentStatus", "autoSoftenerStatus", "waterHard", "delayTime"]

CYCLE_EVENTS = ["started", "paused", "resumed", "finished", "error", "scheduled"]

# parameters that have an entity of their own, created only if the machine reports them
ENTITY_PARAMS = frozenset({"remoteCtrValid", "doorStatus", "saltStatus", "rinseAidStatus", "detWarn", "softWarn",
                           "hobLockStatus", "lockStatus", "currentWaterUsed", "currentElectricityUsed", "temp",
                           "spinSpeed", "dryLevel"})
