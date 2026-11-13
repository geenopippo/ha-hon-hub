"""Turns the hOn command definitions into a simple description of programs, options and settings,
and builds the commands to send the way the official app does. No Home Assistant imports."""
from __future__ import annotations

import copy
import re

SIMPLE_COMMANDS = ("stopProgram", "pauseProgram", "resumeProgram")
# settings of the machine that some models list among the parameters of every program. The official app never
# sends them with a program, so the machine keeps its own value: neither do we, unless the user asks for it.
MACHINE_SETTINGS = {"waterHard"}
# technical parameters that make no sense as an option for the user
HIDDEN_PARAMS = {"lang", "prStr", "onOffStatus", "prCode", "prPosition", "operationName", "checkUpStatus",
                 "favourite", "program", "cloudProgId", "cloudProgSrc", "httpEndpoint", "mqttEndpoint"}


def flat_params(context: dict) -> dict:
    """Current parameters of the appliance (shadow) as {name: value}."""
    shadow = (context or {}).get("shadow") or {}
    return {k: (v or {}).get("parNewVal") for k, v in (shadow.get("parameters") or {}).items()}


def _is_command(d) -> bool:
    return isinstance(d, dict) and "parameters" in d and ("description" in d or "protocolType" in d)


def _param(name: str, d: dict, program: bool = False) -> dict | None:
    """Definition of one parameter. program=True: it belongs to startProgram, where the parameters that are
    not mandatory and have no default (dryTime) are left out unless the user sets them, like the app does."""
    t = d.get("typology")
    p = {"key": name, "type": t, "category": d.get("category"), "mandatory": d.get("mandatory")}
    if t == "fixed":
        p["value"] = d.get("fixedValue")
        if isinstance(p["value"], (dict, list)):
            return None
    elif t == "enum":
        vals = [str(v) for v in (d.get("enumValues") or [])]
        if not vals or any(v.startswith("{") for v in vals):
            return None
        p["values"] = vals
        p["default"] = str(d.get("defaultValue")) if d.get("defaultValue") is not None else vals[0]
    elif t == "range":
        def num(x, dflt):
            try:
                f = float(str(x).replace(",", "."))
                return int(f) if f.is_integer() else f
            except (TypeError, ValueError):
                return dflt
        p["min"] = num(d.get("minimumValue"), 0)
        p["max"] = num(d.get("maximumValue"), p["min"])
        p["step"] = num(d.get("incrementValue"), 1) or 1
        p["default"] = num(d.get("defaultValue"), p["min"])
    else:
        return None
    if program and t != "fixed" and not d.get("mandatory") and (d.get("defaultValue") is None or name in MACHINE_SETTINGS):
        p["optional"] = True
        p["default"] = None
    return p


def _command(d: dict, program: bool = False) -> dict:
    params = {}
    for k, v in (d.get("parameters") or {}).items():
        if isinstance(v, dict) and (p := _param(k, v, program)):
            params[k] = p
    anc = d.get("ancillaryParameters") or {}
    anc_params = {}
    for k, v in anc.items():
        if isinstance(v, dict) and v.get("category") == "general" and v.get("typology") in ("range", "enum"):
            if p := _param(k, v):
                anc_params[k] = {**p, "anc": True}
    return {"params": params, "ancillary_raw": anc, "anc_params": anc_params}


def _with_triggers(c: dict, rules: list[dict]) -> dict:
    """Command parameters + ancillary parameters that trigger rules (e.g. dryOption)."""
    triggers = {k for r in rules for k in r["when"]}
    return {**c["params"], **{k: p for k, p in c["anc_params"].items() if k in triggers and k not in c["params"]}}


def _rules(anc: dict, options: dict) -> list[dict]:
    """programRules -> flat list [{param, when: {key: value}, set: definition}]."""
    raw = (anc.get("programRules") or {})
    raw = raw.get("fixedValue") if isinstance(raw.get("fixedValue"), dict) else None
    out: list[dict] = []
    if not raw:
        return out

    def name(k):
        k = k.replace("@", "").lstrip("+")
        return options.get(k, k)

    def walk(param, trigger, data, when):
        for multi, sub in data.items():
            for val in str(multi).split("|"):
                cond = {**when, name(trigger): val}
                if isinstance(sub, dict) and "typology" in sub:
                    if sub.get("fixedValue") == f"@{param}":
                        continue
                    if p := _param(param, sub):
                        out.append({"param": param, "when": cond, "set": p})
                elif isinstance(sub, dict):
                    for t2, d2 in sub.items():
                        if isinstance(d2, dict):
                            walk(param, t2, d2, cond)
                else:
                    out.append({"param": param, "when": cond, "set": {"key": param, "type": "fixed", "value": str(sub)}})

    for param_key, triggers in raw.items():
        if not isinstance(triggers, dict):
            continue
        for trig, data in triggers.items():
            if isinstance(data, dict):
                walk(name(param_key), trig, data, {})
    return out


def describe(appliance: dict, commands: dict, i18n: dict | None = None) -> dict:
    """Full description of programs, settings and simple commands."""
    i18n = i18n or {}
    options = commands.get("options") if isinstance(commands.get("options"), dict) else {}
    out = {"programs": [], "hidden": [], "settings": None, "simple": []}

    start = commands.get("startProgram") or {}
    if _is_command(start):
        start = {"PROGRAMS.DEFAULT": start}
    for key, d in start.items():
        if not _is_command(d):
            continue
        c = _command(d, program=True)
        anc = c["ancillary_raw"]
        flag = lambda k: str(((anc.get(k) or {}).get("fixedValue")) if isinstance(anc.get(k), dict) else "1")
        fam = (anc.get("programFamily") or {}).get("enumValues") or [] if isinstance(anc.get("programFamily"), dict) else []
        fam = [str(f).strip("[]") for f in fam]
        maint = any(f in ("maintenance", "diagnostic") for f in fam)
        # the programs the app does not list (it reaches them from its guided choices or by voice) are kept
        # apart: not offered in the lists, but known by key and code. Maintenance and diagnostic cycles are
        # listed even if hidden: the official app shows them in its Maintenance section
        hidden = flag("remoteVisible") == "0" and not maint
        pid = key.split(".")[-1].lower()
        rules = _rules(anc, options)
        allp = _with_triggers(c, rules)
        out["hidden" if hidden else "programs"].append({
            "key": key, "id": pid,
            "name": i18n.get(key) or pid.replace("iot_", "").replace("_", " ").capitalize(),
            "desc": i18n.get(key + "_DESCRIPTION", ""),
            "remote": flag("remoteActionable") != "0",
            "family": fam,
            "maintenance": maint,
            "params": [p for p in allp.values() if p["type"] != "fixed" and p["key"] not in HIDDEN_PARAMS],
            "fixed": {k: p.get("value") for k, p in c["params"].items() if p["type"] == "fixed"},
            "rules": rules,
            "prCode": c["params"].get("prCode", {}).get("value"),
        })
    out["programs"].sort(key=lambda p: p["name"].lower())
    out["hidden"].sort(key=lambda p: p["name"].lower())

    settings = commands.get("settings") or {}
    if not _is_command(settings):
        settings = settings.get("setParameters") or next((v for v in settings.values() if _is_command(v)), None) or {}
    if _is_command(settings):
        c = _command(settings)
        visible = [p for p in c["params"].values() if p["type"] != "fixed" and p["key"] not in HIDDEN_PARAMS]
        if visible:
            out["settings"] = {"params": visible}

    for name in SIMPLE_COMMANDS:
        if _is_command(commands.get(name)):
            out["simple"].append(name)
    return out


# ── building the commands ──────────────────────────────────────────────────

def _coerce(p: dict, value):
    """Validate a value against its definition; ValueError if it is not allowed."""
    if p["type"] == "fixed":
        return str(p.get("value") if p.get("value") is not None else "")
    if p["type"] == "enum":
        v = str(value)
        if v not in p["values"]:
            raise ValueError(f"{p['key']}: {v} is not one of {', '.join(p['values'])}")
        return v
    try:
        f = float(str(value).replace(",", "."))
    except ValueError:
        raise ValueError(f"{p['key']}: {value} is not a number")
    if not (p["min"] <= f <= p["max"]):
        raise ValueError(f"{p['key']}: {value} is outside {p['min']}-{p['max']}")
    step = p["step"] or 1
    if abs(((f - p["min"]) / step) - round((f - p["min"]) / step)) > 1e-6:
        raise ValueError(f"{p['key']}: {value} is not a multiple of {step}")
    return str(int(f)) if f.is_integer() else str(f)


def apply_rules(defs: dict, values: dict, rules: list[dict]) -> tuple[dict, dict]:
    """Apply the rules of the program. Returns (effective definitions, values)."""
    eff = copy.deepcopy(defs)
    vals = dict(values)
    for _ in range(5):
        changed = False
        eff = copy.deepcopy(defs)
        for r in rules:
            if all(str(vals.get(k)) == str(v) for k, v in r["when"].items()) and r["param"] in eff:
                forced = {**r["set"], "key": r["param"]}
                if defs[r["param"]].get("optional") and forced["type"] != "fixed":
                    forced.update(optional=True, default=None)      # an optional parameter stays optional
                eff[r["param"]] = forced
        for k, p in eff.items():
            cur = vals.get(k)
            try:
                new = _coerce(p, cur) if cur is not None else None
            except ValueError:
                new = None
            if new is None and not p.get("optional"):
                new = _coerce(p, p.get("value") if p["type"] == "fixed" else p["default"])
            if (None if cur is None else str(cur)) != new:
                vals[k] = new
                changed = True
        if not changed:
            break
    return eff, vals


def _ancillary(anc: dict, chosen: dict | None = None) -> dict:
    chosen = chosen or {}
    out = {}
    for k, d in anc.items():
        if k == "programRules" or not isinstance(d, dict):
            continue
        if d.get("category") == "rule":
            continue
        t = d.get("typology")
        if k in chosen:
            out[k] = chosen[k]
        elif t == "fixed":
            out[k] = d.get("fixedValue")
        elif t == "enum":
            out[k] = "[" + "|".join(str(v) for v in d.get("enumValues") or []) + "]"
        elif t == "range":
            out[k] = d.get("defaultValue", d.get("minimumValue"))
    return out


def build_start(commands: dict, program_key: str, user_values: dict) -> dict:
    start = commands.get("startProgram") or {}
    d = start if _is_command(start) else start.get(program_key)
    if not _is_command(d):
        raise ValueError("program not found")
    options = commands.get("options") if isinstance(commands.get("options"), dict) else {}
    c = _command(d, program=True)
    rules = _rules(c["ancillary_raw"], options)
    defs = _with_triggers(c, rules)
    for k in user_values:
        if k not in defs or k in HIDDEN_PARAMS or defs[k]["type"] == "fixed":
            raise ValueError(f"{k}: not an option of this program")
    values = {k: (p.get("value") if p["type"] == "fixed" else p["default"]) for k, p in defs.items()}
    wanted = {k: _coerce(defs[k], v) for k, v in user_values.items()}
    values.update(wanted)
    _, values = apply_rules(defs, values, rules)
    # what the user asked for must be what is sent: a rule of the program that overrides it is an error,
    # not a silent change (asking for a drying level without turning drying on would wash and not dry)
    for k, v in wanted.items():
        if values.get(k) != v:
            asked = {**values, **wanted}
            rule = next((r for r in rules if r["param"] == k
                         and all(str(asked.get(a)) == str(b) for a, b in r["when"].items())), None)
            why = " with " + ", ".join(f"{a}={b}" for a, b in rule["when"].items()) if rule else ""
            raise ValueError(f"{k}: {v} is not possible{why}")
    if "dryTime" in wanted and str(values.get("dryOption")) == "0":
        raise ValueError("dryTime: needs dryOption=1")
    # like the app: fixed parameters without a value (prStr) are not sent
    params = {k: str(v) for k, v in values.items() if v not in (None, "") and k in c["params"]}
    chosen = {k: str(v) for k, v in values.items() if k not in c["params"] and v is not None}
    return {"command": "startProgram", "parameters": params,
            "ancillary": _ancillary(c["ancillary_raw"], chosen), "options": options,
            "program_name": "" if program_key == "PROGRAMS.DEFAULT" else program_key}


def build_settings(commands: dict, current: dict, user_values: dict) -> dict:
    settings = commands.get("settings") or {}
    if not _is_command(settings):
        settings = settings.get("setParameters") or next((v for v in settings.values() if _is_command(v)), None) or {}
    if not _is_command(settings):
        raise ValueError("this appliance has no settings")
    c = _command(settings)
    for k in user_values:
        if k not in c["params"] or k in HIDDEN_PARAMS or c["params"][k]["type"] == "fixed":
            raise ValueError(f"{k}: not a setting of this appliance")
    params = {}
    for k, p in c["params"].items():
        if p["type"] == "fixed":
            v = p.get("value")
        elif k in user_values:
            v = _coerce(p, user_values[k])
        else:
            try:
                v = _coerce(p, current.get(k, p["default"]))
            except ValueError:
                v = _coerce(p, p["default"])
        if v not in (None, ""):
            params[k] = str(v)
    return {"command": "settings", "parameters": params, "ancillary": _ancillary(c["ancillary_raw"]),
            "options": commands.get("options") if isinstance(commands.get("options"), dict) else {}}


def build_simple(commands: dict, name: str) -> dict:
    if name not in SIMPLE_COMMANDS or not _is_command(commands.get(name)):
        raise ValueError("command not available")
    c = _command(commands[name])
    params = {k: str(p.get("value") if p["type"] == "fixed" else p["default"]) for k, p in c["params"].items()}
    return {"command": name, "parameters": params, "ancillary": _ancillary(c["ancillary_raw"]),
            "options": commands.get("options") if isinstance(commands.get("options"), dict) else {}}


def program_i18n(tr: dict) -> dict:
    """From the hOn translation file keep only the names and descriptions of the programs."""
    out = {}

    def walk(prefix, node):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        elif isinstance(node, str):
            out[prefix] = node
    walk("PROGRAMS", tr.get("PROGRAMS") or {})
    return {k: v for k, v in out.items() if not re.search(r"_(NAME|TITLE)_", k)}
