"""Diagnostics: what the integration knows, without anything that identifies the account or the machines."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import HonConfigEntry

REDACT = {"email", "password", "mobile_id", "macAddress", "serialNumber", "coords", "PK", "SK", "id", "applianceId",
          "nickName", "code", "sfPersonAccountId", "principalUserId", "topics", "transactionId", "device", "mobileId",
          "purchaseDate", "enrollmentDate", "firstEnrollment", "firstEnrollmentTBC", "lastUpdate", "attributes"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: HonConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    rt = coordinator.realtime
    appliances = []
    for a in coordinator.appliances.values():
        options = {key: sorted(kinds) for key, kinds in a.option_kinds().items()}
        appliances.append({
            "info": async_redact_data(a.info, REDACT),
            "connected": a.connected, "status": a.status, "remaining": a.remaining, "phase": a.phase,
            "program": a.program_name, "error_code": a.error_code, "params": a.params,
            "activity": async_redact_data({k: v for k, v in a.activity.items() if k != "macAddress"}, REDACT - {"attributes"}),
            "last_command": async_redact_data({k: a.last_command.get(k) for k in ("commandName", "programName", "parameters",
                                                                                 "timestamp")}, REDACT),
            "maintenance": a.maintenance, "statistics": a.statistics,
            "programs": [{"key": p["key"], "name": p["name"], "prCode": p.get("prCode"),
                          "prPosition": p["fixed"].get("prPosition"), "family": p["family"], "remote": p["remote"],
                          "options": [d["key"] for d in p["params"]], "rules": len(p["rules"])} for p in a.programs],
            "options": options, "settings": a.settings, "simple_commands": a.simple_commands,
            "draft": {"program": a.draft_key, "values": a.draft_values}, "live_messages": a.live_messages,
        })
    return {
        "entry": async_redact_data(dict(entry.data), REDACT),
        "realtime": None if rt is None else {"connected": rt.connected, "messages": rt.messages,
                                             "last_message": rt.last_message, "last_error": rt.last_error},
        "update_interval": str(coordinator.update_interval),
        "program_names": len(coordinator.i18n),
        "appliances": appliances,
    }
