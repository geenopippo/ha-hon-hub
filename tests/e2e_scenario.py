"""End-to-end scenario: a real Home Assistant (test container) with the integration, against the fake cloud.

Run tests/e2e_ha.py setup first (see its docstring). Every step prints what it checked; the first failure stops.
"""
import asyncio
import json
import subprocess
import sys

import aiohttp

from e2e_ha import HA

DW, WD, IH = "lavastoviglie", "lavasciuga", "piano_cottura"


def ok(text):
    print("  ok ", text, flush=True)


async def main():
    async with aiohttp.ClientSession() as session:
        ha = HA(session)
        cloud = await ha.cloud("/_test/state")
        mac = {kind: m for m, kind in cloud["macs"].items()}
        assert cloud["sockets"] == 1, cloud["sockets"]

        async def push(kind, params):
            await ha.cloud(f"/_test/params/{mac[kind]}", params)

        async def sent():
            return (await ha.cloud("/_test/state"))["sent"]

        async def expect(entity, value, seconds=10):
            assert await ha.wait(entity, value, seconds), f"{entity} = {await ha.state(entity)!r}, expected {value!r}"

        async def fails(coro, status):
            try:
                await coro
            except RuntimeError as err:
                assert f"HTTP {status}" in str(err), err
                return str(err)
            raise AssertionError("the call should have failed")

        print("1. first read")
        await expect(f"sensor.{DW}_stato", "running")
        await expect(f"sensor.{WD}_programma", "Eco 40-60")
        await expect(f"sensor.{IH}_stato", "offline")
        ok("states read from the cloud")

        print("2. real-time updates (no polling involved: they must arrive in under 2 s)")
        await push("DW", {"remainingTimeMM": "100", "prPhase": "2"})
        await expect(f"sensor.{DW}_tempo_residuo", "100", 2)
        await expect(f"sensor.{DW}_fase", "washing", 2)
        ok("remaining time and phase pushed")
        await push("DW", {"machMode": "7", "prCode": "0"})       # like the real machines: the code is cleared at the end
        await expect(f"sensor.{DW}_stato", "finished", 2)
        ev = (await ha.states(f"{DW}_ciclo"))[f"event.{DW}_ciclo"]
        assert ev["attributes"]["event_type"] == "finished" and ev["attributes"]["program"] == "Igienizzante", ev
        assert await ha.state(f"sensor.{DW}_programma") == "unknown" and await ha.state(f"sensor.{DW}_tempo_residuo") == "0"
        ok("end of cycle: state finished, event 'finished' with the program name")

        print("3. presence")
        await ha.cloud(f"/_test/presence/{mac['WD']}", {"connected": False})
        await expect(f"sensor.{WD}_stato", "offline", 2)
        await expect(f"button.{WD}_avvia_programma", "unavailable", 2)
        await ha.cloud(f"/_test/presence/{mac['WD']}", {"connected": True})
        await expect(f"sensor.{WD}_stato", "running", 6)
        ok("offline: controls unavailable; back online: state read again")
        await ha.cloud(f"/_test/presence/{mac['IH']}", {"connected": True})
        await push("IH", {"onOffStatusZ2": "1", "powerZ2": "6", "hotStatusZ2": "1"})
        await expect(f"sensor.{IH}_stato", "on", 3)
        await expect(f"sensor.{IH}_zona_2_potenza", "6", 2)
        await expect(f"binary_sensor.{IH}_zona_2_calore_residuo", "on", 2)
        ok("hob zones")

        print("4. program with options from the entities")
        await push("DW", {"machMode": "1"})
        await expect(f"sensor.{DW}_stato", "ready", 2)
        assert await ha.state(f"number.{DW}_opzione_partenza_ritardata") == "unavailable"      # no program chosen yet
        err = await fails(ha.call("button.press", {"entity_id": f"button.{DW}_avvia_programma"}), 400)
        assert "Program to start" in err, err            # the REST API answers in English, the UI translates
        await ha.call("select.select_option", {"entity_id": f"select.{DW}_programma_da_avviare", "option": "Eco"})
        await expect(f"number.{DW}_opzione_partenza_ritardata", "0", 2)
        await ha.call("switch.turn_on", {"entity_id": f"switch.{DW}_opzione_mezzo_carico"})
        await ha.call("number.set_value", {"entity_id": f"number.{DW}_opzione_partenza_ritardata", "value": 60})
        await fails(ha.call("number.set_value", {"entity_id": f"number.{DW}_opzione_partenza_ritardata", "value": 61}), 400)
        before = len(await sent())
        await ha.call("button.press", {"entity_id": f"button.{DW}_avvia_programma"})
        cmd = (await sent())[-1]
        assert len(await sent()) == before + 1 and cmd["commandName"] == "startProgram", cmd
        assert cmd["programName"] == "PROGRAMS.DW.ECO" and cmd["parameters"]["halfLoad"] == "1", cmd
        assert cmd["parameters"]["delayTime"] == "60" and cmd["macAddress"] == mac["DW"], cmd
        await expect(f"sensor.{DW}_stato", "scheduled", 3)
        await expect(f"sensor.{DW}_programma", "Eco", 3)
        ev = (await ha.states(f"{DW}_ciclo"))[f"event.{DW}_ciclo"]["attributes"]
        assert ev["event_type"] == "scheduled" and ev["program"] == "Eco", ev
        ok("start sent with the chosen options; state and event follow, with the right program name")
        await ha.call("button.press", {"entity_id": f"button.{DW}_interrompi"})
        assert (await sent())[-1]["commandName"] == "stopProgram"
        await expect(f"sensor.{DW}_stato", "ready", 3)
        ok("stop")

        print("5. rules between options (washer dryer)")
        before = len(await sent())
        await ha.call("select.select_option", {"entity_id": f"select.{WD}_programma_da_avviare", "option": "Care 30'"})
        await fails(ha.call("button.press", {"entity_id": f"button.{WD}_avvia_programma"}), 500)
        assert len(await sent()) == before                                   # a cycle is running: nothing is sent
        await push("WD", {"machMode": "1"})
        await expect(f"sensor.{WD}_stato", "ready", 2)
        await ha.call("select.select_option", {"entity_id": f"select.{WD}_programma_da_avviare", "option": "Care 30'"})
        await expect(f"switch.{WD}_opzione_asciuga_dopo_il_lavaggio", "off", 2)
        await expect(f"select.{WD}_opzione_livello_di_asciugatura", "unavailable", 2)
        await ha.call("switch.turn_on", {"entity_id": f"switch.{WD}_opzione_asciuga_dopo_il_lavaggio"})
        level = (await ha.states(f"{WD}_opzione_livello_di_asciugatura"))[f"select.{WD}_opzione_livello_di_asciugatura"]
        assert level["state"] != "unavailable" and "3" in level["attributes"]["options"], level
        assert level["state"] != "0", level                                  # drying on: the default level, not "none"
        await ha.call("select.select_option", {"entity_id": f"select.{WD}_opzione_livello_di_asciugatura", "option": "3"})
        await ha.call("select.select_option", {"entity_id": f"select.{WD}_opzione_temperatura", "option": "40"})
        await ha.call("button.press", {"entity_id": f"button.{WD}_avvia_programma"})
        cmd = (await sent())[-1]
        assert cmd["programName"] == "PROGRAMS.WM_WD.CARE_30" and cmd["parameters"]["dryLevel"] == "3", cmd
        assert cmd["parameters"]["temp"] == "40" and cmd["ancillaryParameters"]["dryOption"] == "1", cmd
        assert cmd["attributes"]["prStr"] == "Care 30'" and "dryTime" not in cmd["parameters"], cmd
        ok("drying level appears only with 'dry after washing'; command built like the official app does")
        for name in ("pausa", "riprendi"):
            await ha.call("button.press", {"entity_id": f"button.{WD}_{name}"})
        assert [c["commandName"] for c in (await sent())[-2:]] == ["pauseProgram", "resumeProgram"]
        ok("pause and resume")

        print("6. remote control off / appliance offline")
        await push("WD", {"remoteCtrValid": "0", "machMode": "1"})
        await expect(f"binary_sensor.{WD}_controllo_remoto", "off", 2)
        before = len(await sent())
        err = await fails(ha.call("button.press", {"entity_id": f"button.{WD}_avvia_programma"}), 500)
        assert len(await sent()) == before
        await push("WD", {"remoteCtrValid": "1"})
        ok("nothing is sent while the remote control is off")

        print("7. settings")
        await ha.call("switch.turn_on", {"entity_id": f"switch.{DW}_luce_interna"})
        cmd = (await sent())[-1]
        assert cmd["commandName"] == "settings" and cmd["parameters"]["lightStatus"] == "1", cmd
        assert cmd["parameters"]["waterHard"] == "4", cmd                    # the other settings keep their value
        await ha.call("switch.turn_off", {"entity_id": f"switch.{DW}_luce_interna"})
        before = len(await sent())
        await asyncio.gather(                                                # two settings changed at the same moment
            ha.call("switch.turn_on", {"entity_id": f"switch.{DW}_luce_interna"}),
            ha.call("number.set_value", {"entity_id": f"number.{DW}_durezza_dell_acqua", "value": 6}))
        cmds = (await sent())[before:]
        assert len(cmds) == 2 and cmds[-1]["parameters"]["waterHard"] == "6" and cmds[-1]["parameters"]["lightStatus"] == "1", cmds
        await expect(f"switch.{DW}_luce_interna", "on", 2)
        await expect(f"number.{DW}_durezza_dell_acqua", "6", 2)
        ok("two settings changed together: the second command carries both")

        print("8. maintenance")
        await expect(f"binary_sensor.{DW}_manutenzione_da_fare", "on")
        await ha.call("button.press", {"entity_id": f"button.{DW}_azzera_pulizia_filtro"})
        cmd = (await sent())[-1]
        assert cmd["commandName"] == "resetCounter" and cmd["counterType"] == "filterCleaning", cmd
        await expect(f"sensor.{DW}_pulizia_filtro_cicli_rimanenti", "15", 2)
        await expect(f"binary_sensor.{DW}_manutenzione_da_fare", "off", 2)
        await ha.call("button.press", {"entity_id": f"button.{WD}_azzera_pulizia_filtro"})
        assert (await sent())[-1]["commandName"] == "filterCleaning"         # washers use the notification service
        await expect(f"sensor.{WD}_pulizia_filtro_cicli_rimanenti", "100", 2)
        await ha.call("number.set_value", {"entity_id": f"number.{DW}_imposta_pastiglie", "value": 30})
        await expect(f"sensor.{DW}_pastiglie_rimaste", "30", 2)
        ok("counters reset with the same calls as the app; tablets set")

        print("9. service hon_hub.start_program")
        device = await ha.template("{{ device_id('sensor.%s_stato') }}" % WD)
        before = len(await sent())
        res = await ha.call("hon_hub.start_program", {"device_id": device, "program": "eco_40_60_new_energy_label",
                                                      "options": {"temp": 40, "delayTime": 120}, "validate_only": True}, response=True)
        res = res["service_response"]
        assert res["sent"] is False and res["program"] == "Eco 40-60" and res["parameters"]["temp"] == "40", res
        assert len(await sent()) == before
        ok("validate_only builds the command and sends nothing")
        err = await fails(ha.call("hon_hub.start_program", {"device_id": device, "program": "Eco 40-60", "options": {"temp": 95}}), 400)
        assert "95" in err, err
        err = await fails(ha.call("hon_hub.start_program", {"device_id": device, "program": "care_30", "options": {"dryLevel": 3}}), 400)
        assert "dryOption=0" in err, err                                     # a level without drying on: refused, not dropped
        await fails(ha.call("hon_hub.start_program", {"device_id": device, "program": "non esiste"}), 400)
        await fails(ha.call("hon_hub.start_program", {"device_id": "nope", "program": "eco"}), 400)
        hob = await ha.template("{{ device_id('sensor.%s_stato') }}" % IH)
        before = len(await sent())
        await fails(ha.call("hon_hub.start_program", {"device_id": hob, "program": "iot_standard_boiling"}), 400)
        assert len(await sent()) == before                                   # nothing is ever sent to the hob
        res = await ha.call("hon_hub.start_program", {"device_id": device, "program": "Eco 40-60", "options": {"spinSpeed": "800"}},
                            response=True)
        cmd = (await sent())[-1]
        assert res["service_response"]["sent"] is True and cmd["parameters"]["spinSpeed"] == "800", cmd
        ok("wrong option, wrong program, wrong device and the hob are refused; a valid call is sent")

        print("10. expired tokens and dropped channel")
        refreshes = (await ha.cloud("/_test/state"))["refreshes"]
        await ha.cloud("/_test/expire", {})
        await ha.call("button.press", {"entity_id": f"button.{WD}_pausa"})
        state = await ha.cloud("/_test/state")
        assert state["refreshes"] == refreshes + 1 and state["logins"] <= 2, state
        ok("tokens renewed with the session, no new password login")
        await ha.cloud("/_test/drop", {})
        for _ in range(100):
            if (await ha.cloud("/_test/state"))["sockets"] == 1:
                break
            await asyncio.sleep(0.2)
        else:
            raise AssertionError("the real-time channel did not come back")
        await asyncio.sleep(0.5)
        await push("DW", {"remainingTimeMM": "77", "machMode": "2"})
        await expect(f"sensor.{DW}_tempo_residuo", "77", 3)
        ok("channel reconnected by itself and pushing again")

        print("11. diagnostics, reload")
        entry = next(e for e in await ha.get("/api/config/config_entries/entry") if e["domain"] == "hon_hub")
        diag = json.dumps(await ha.get(f"/api/diagnostics/config_entry/{entry['entry_id']}"))
        for secret in (mac["DW"], mac["WD"], "casa@example.com", "segreta"):
            assert secret not in diag, f"{secret} leaked in the diagnostics"
        assert '"realtime": {"connected": true' in diag and "PROGRAMS.DW.ECO" in diag
        ok(f"diagnostics download ({len(diag) // 1024} kB) without account or mac addresses")
        await push("WD", {"machMode": "1"})
        await ha.cloud(f"/_test/presence/{mac['WD']}", {"connected": False})     # the washer dryer is switched off
        await expect(f"sensor.{WD}_stato", "offline", 3)
        await ha.post(f"/api/config/config_entries/entry/{entry['entry_id']}/reload")
        await expect(f"sensor.{DW}_stato", "running")
        await asyncio.sleep(1.5)
        assert (await ha.cloud("/_test/state"))["sockets"] == 1
        assert await ha.state(f"select.{DW}_programma_da_avviare") == "Eco"            # the choice survives a reload
        ok("reload: one channel open, chosen program restored")
        stamp = await ha.state(f"event.{WD}_ciclo")
        await ha.cloud(f"/_test/presence/{mac['WD']}", {"connected": True})      # switched on and started from its panel
        await push("WD", {"machMode": "2", "prCode": "42", "prPosition": "3"})
        await expect(f"sensor.{WD}_stato", "running", 5)
        ev = (await ha.states(f"{WD}_ciclo"))[f"event.{WD}_ciclo"]
        assert ev["state"] != stamp and ev["attributes"]["event_type"] == "started", ev
        ok("a machine that was off when Home Assistant started still fires 'started'")

        print("12. password changed in the app -> re-authentication")
        await ha.cloud("/_test/password", {"password": "nuova"})
        for _ in range(250):
            flows = [f for f in await ha.ws({"type": "config_entries/flow/progress"}) if f["handler"] == "hon_hub"]
            if flows:
                break
            await asyncio.sleep(0.2)
        else:
            raise AssertionError("no re-authentication flow started")
        assert flows[0]["context"]["source"] == "reauth", flows
        bad = await ha.post(f"/api/config/config_entries/flow/{flows[0]['flow_id']}", {"email": "casa@example.com", "password": "boh"})
        assert bad["errors"] == {"base": "invalid_auth"}, bad
        good = await ha.post(f"/api/config/config_entries/flow/{flows[0]['flow_id']}", {"email": "casa@example.com", "password": "nuova"})
        assert good["type"] == "abort" and good["reason"] == "reauth_successful", good
        await expect(f"sensor.{DW}_stato", "running")
        ok("wrong password refused, right one accepted, integration running again")

        log = subprocess.run(["docker", "logs", "ha-hon-test"], capture_output=True, text=True)
        text = log.stdout + log.stderr
        bad = [line for line in text.splitlines()
               if any(w in line for w in ("ERROR", "blocking call", "Task exception", "Unexpected", "never awaited"))]
        # the errors this scenario causes on purpose: two refused commands and the changed password
        allowed = ("Error during service call to button.press: Remote control is off",
                   "Error during service call to button.press: The appliance already has a cycle running",
                   "Authentication failed while fetching hon_hub data")
        unexpected = [line for line in bad if not any(a in line for a in allowed)]
        assert not unexpected, "\n".join(unexpected[:20])
        # the changed password is noticed either by the real-time channel (no log) or by the polling (one logged error)
        expected = 2 if "Authentication failed while fetching hon_hub data" in text else 0
        assert text.count("Traceback (most recent call last)") == expected, "unexpected traceback in the log"
        ok("nothing unexpected in the Home Assistant log")
        print("ALL GOOD")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as err:
        print("FAILED:", err)
        sys.exit(1)
