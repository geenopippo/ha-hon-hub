import json
from pathlib import Path

import pytest

from custom_components.hon_hub import const, model

FIX = Path(__file__).parent / "fixtures"


def load(name, file):
    return json.loads((FIX / name / f"{file}.json").read_text())


@pytest.fixture(scope="module")
def wd():
    return load("wd_209", "commands")


@pytest.fixture(scope="module")
def dw():
    return load("dw_12658", "commands")


def test_describe_washer(wd):
    d = model.describe({}, wd)
    assert len(d["programs"]) > 50
    assert d["simple"] == ["stopProgram", "pauseProgram", "resumeProgram"]
    care = next(p for p in d["programs"] if p["key"] == "PROGRAMS.WM_WD.CARE_30")
    keys = [p["key"] for p in care["params"]]
    assert "temp" in keys and "spinSpeed" in keys and "dryOption" in keys
    assert "lang" not in keys and "prCode" not in keys


def test_start_default_has_no_drying(wd):
    b = model.build_start(wd, "PROGRAMS.WM_WD.CARE_30", {})
    assert b["parameters"]["dryLevel"] == "0"
    assert b["ancillary"]["dryOption"] == "0"
    assert b["program_name"] == "PROGRAMS.WM_WD.CARE_30"
    assert "programRules" not in b["ancillary"]
    assert "prStr" not in b["parameters"]


def test_start_with_drying(wd):
    b = model.build_start(wd, "PROGRAMS.WM_WD.CARE_30", {"dryOption": "1", "dryLevel": "3", "temp": "40"})
    assert b["parameters"]["dryLevel"] == "3"
    assert b["parameters"]["temp"] == "40"
    assert b["ancillary"]["dryOption"] == "1"


def test_start_rejects_invalid_values(wd):
    with pytest.raises(ValueError):
        model.build_start(wd, "PROGRAMS.WM_WD.CARE_30", {"temp": "95"})
    with pytest.raises(ValueError):
        model.build_start(wd, "PROGRAMS.WM_WD.CARE_30", {"delayTime": "45"})   # step 30
    with pytest.raises(ValueError):
        model.build_start(wd, "PROGRAMS.WM_WD.CARE_30", {"sconosciuto": "1"})
    with pytest.raises(ValueError):
        model.build_start(wd, "PROGRAMS.NON.ESISTE", {})


def test_rules_map_option_names_and_apply():
    # in the rules the options are called opt1..optN: they are translated with the "options" map
    cmds = load("dw_509", "commands")
    d = model.describe({}, cmds)
    prog = next(p for p in d["programs"] if p["rules"])
    names = set(cmds["options"].values())
    r = prog["rules"][0]
    assert r["param"] in names and all(k in names for k in r["when"])
    # turning on the option that triggers the rule forces the other one
    trig, val = next(iter(r["when"].items()))
    if r["set"]["type"] == "fixed" and trig in {p["key"] for p in prog["params"]}:
        b = model.build_start(cmds, prog["key"], {trig: val})
        assert b["parameters"][r["param"]] == r["set"]["value"]


def test_settings_and_simple(dw):
    s = model.build_settings(dw, {"waterHard": "3", "multiDosingLevelRA": "5"}, {"lightStatus": "1"})
    assert s["parameters"]["waterHard"] == "3"           # current value kept
    assert s["parameters"]["multiDosingLevelRA"] == "5"
    assert s["parameters"]["lightStatus"] == "1"
    assert s["command"] == "settings"
    with pytest.raises(ValueError):
        model.build_settings(dw, {}, {"waterHard": "9"})
    assert model.build_simple(dw, "stopProgram")["parameters"] == {"onOffStatus": "0"}
    with pytest.raises(ValueError):
        model.build_simple(dw, "deleteEverything")


def test_flat_params():
    ctx = load("wd_209", "attributes")
    p = model.flat_params(ctx)
    assert p["machMode"] == "2" and p["remainingTimeMM"] == "163"


def test_hidden_maintenance_programs_are_kept():
    cmds = load("dw_12658", "commands")
    d = model.describe({}, cmds)
    ids = {p["id"]: p for p in d["programs"]}
    assert "dishwasher_care" in ids and ids["dishwasher_care"]["maintenance"]
    assert "iot_voice_eco" not in ids          # hidden and not maintenance: dropped


def test_resettable_counters_match_official_app():
    # from the code of the hOn app 2.31.10 (onPressPrimaryButton -> resetCounterDW)
    assert set(const.RESETTABLE["DW"]) == {"filterCleaning", "sprayArmsCleaning", "drawerCleaning"}
    assert const.RESETTABLE["WD"] == {"filterCleaning": "notification", "drawerCleaning": "commands"}
    assert all("drumCleaning" not in v for v in const.RESETTABLE.values())


def test_optional_parameters_are_left_out_like_the_official_app(wd):
    # dryTime is not mandatory and has no default: the app sends it only when the user asks for timed drying
    d = model.describe({}, wd)
    care = next(p for p in d["programs"] if p["key"] == "PROGRAMS.WM_WD.CARE_30")
    dry_time = next(p for p in care["params"] if p["key"] == "dryTime")
    assert dry_time["optional"] and dry_time["default"] is None
    assert "dryTime" not in model.build_start(wd, "PROGRAMS.WM_WD.CARE_30", {})["parameters"]
    b = model.build_start(wd, "PROGRAMS.WM_WD.CARE_30", {"dryOption": "1", "dryTime": "2"})
    assert b["parameters"]["dryTime"] == "2"
    with pytest.raises(ValueError):
        model.build_start(wd, "PROGRAMS.WM_WD.CARE_30", {"dryTime": "9"})
    eff, vals = model.apply_rules({"dryTime": dry_time}, {"dryTime": "9"}, [])     # a value no longer valid is dropped
    assert vals["dryTime"] is None


def test_machine_settings_are_not_sent_with_a_program(wd):
    # some models list the water hardness among the parameters of every program, with a default: sending the
    # default at every start would overwrite the value set on the machine. The official app never sends it.
    import copy
    cmds = copy.deepcopy(wd)
    cmds["startProgram"]["PROGRAMS.WM_WD.CARE_30"]["parameters"]["waterHard"] = {
        "category": "command", "typology": "range", "mandatory": 0, "defaultValue": "1", "minimumValue": "1",
        "maximumValue": "3", "incrementValue": "1"}
    assert "waterHard" not in model.build_start(cmds, "PROGRAMS.WM_WD.CARE_30", {})["parameters"]
    assert model.build_start(cmds, "PROGRAMS.WM_WD.CARE_30", {"waterHard": "3"})["parameters"]["waterHard"] == "3"


def test_settings_still_send_every_value(dw):
    # in the settings command nothing is optional: every setting goes with its current value
    s = model.build_settings(dw, {}, {"lightStatus": "1"})
    assert s["parameters"]["waterHard"] == "4" and s["parameters"]["buzzerDisabled"] == "0"
