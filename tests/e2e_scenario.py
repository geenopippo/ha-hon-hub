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
        await push("DW", {"machMode": "7"})
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
        ok("start sent with the chosen options; state follows")
        await ha.call("button.press", {"entity_id": f"button.{DW}_interrompi"})
        assert (await sent())[-1]["commandName"] == "stopProgram"
        await expect(f"sensor.{DW}_stato", "ready", 3)
        ok("stop")

        print("5. rules between options (washer dryer)")
        await ha.call("select.select_option", {"entity_id": f"select.{WD}_programma_da_avviare", "option": "Care 30'"})
        await expect(f"switch.{WD}_opzione_asciuga_dopo_il_lavaggio", "off", 2)
        await expect(f"select.{WD}_opzione_livello_di_asciugatura", "unavailable", 2)
        await ha.call("switch.turn_on", {"entity_id": f"switch.{WD}_opzione_asciuga_dopo_il_lavaggio"})
        level = (await ha.states(f"{WD}_opzione_livello_di_asciugatura"))[f"select.{WD}_opzione_livello_di_asciugatura"]
        assert level["state"] != "unavailable" and "3" in level["attributes"]["options"], level
        await ha.call("select.select_option", {"entity_id": f"select.{WD}_opzione_livello_di_asciugatura", "option": "3"})
        await ha.call("select.select_option", {"entity_id": f"select.{WD}_opzione_temperatura", "option": "40"})
        await ha.call("button.press", {"entity_id": f"button.{WD}_avvia_programma"})
        cmd = (await sent())[-1]
        assert cmd["programName"] == "PROGRAMS.WM_WD.CARE_30" and cmd["parameters"]["dryLevel"] == "3", cmd
        assert cmd["parameters"]["temp"] == "40" and cmd["ancillaryParameters"]["dryOption"] == "1", cmd
        ok("drying level appears only with 'dry after washing'; command built with the rule applied")
        for name in ("pausa", "riprendi"):
            await ha.call("button.press", {"entity_id": f"button.{WD}_{name}"})
        assert [c["commandName"] for c in (await sent())[-2:]] == ["pauseProgram", "resumeProgram"]
        ok("pause and resume")

        print("6. remote control off / appliance offline")
        await push("WD", {"remoteCtrValid": "0"})
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
        await ha.call("number.set_value", {"entity_id": f"number.{DW}_durezza_dell_acqua", "value": 6})
        cmd = (await sent())[-1]
        assert cmd["parameters"]["waterHard"] == "6" and cmd["parameters"]["lightStatus"] == "1", cmd
        await expect(f"switch.{DW}_luce_interna", "on", 2)
        await expect(f"number.{DW}_durezza_dell_acqua", "6", 2)
        ok("a second setting sent right after the first does not undo it")

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
        ok("wrong option, wrong program and wrong device are refused; a valid call is sent")

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
        await ha.post(f"/api/config/config_entries/entry/{entry['entry_id']}/reload")
        await expect(f"sensor.{DW}_stato", "running")
        await asyncio.sleep(1.5)
        assert (await ha.cloud("/_test/state"))["sockets"] == 1
        assert await ha.state(f"select.{DW}_programma_da_avviare") == "Eco"            # the choice survives a reload
        ok("reload: one channel open, chosen program restored")

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
        # the two errors this scenario causes on purpose: a refused command and the changed password
        allowed = ("Error during service call to button.press: Remote control is off",
                   "Authentication failed while fetching hon_hub data")
        unexpected = [line for line in bad if not any(a in line for a in allowed)]
        assert not unexpected, "\n".join(unexpected[:20])
        assert text.count("Traceback (most recent call last)") == 2, "unexpected traceback in the log"   # the auth one
        ok("nothing unexpected in the Home Assistant log")
        print("ALL GOOD")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as err:
        print("FAILED:", err)
        sys.exit(1)
