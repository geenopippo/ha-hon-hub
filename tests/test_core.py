"""Client, real-time channel and appliance state against the fake cloud (no Home Assistant needed)."""
import asyncio
import json
import time
from pathlib import Path

import aiohttp
import pytest

import fake_cloud
from custom_components.hon_hub import appliance as appliance_mod, hon, model, realtime
from custom_components.hon_hub.appliance import Appliance, param_kind, param_values

FIX = Path(__file__).parent / "fixtures"


def load(name, file):
    return json.loads((FIX / name / f"{file}.json").read_text())


def run(coro):
    return asyncio.run(coro)


class Env:
    """Fake cloud + a client connected to it."""

    def __init__(self, password=fake_cloud.PASSWORD, **cloud_args):
        self.cloud = fake_cloud.FakeCloud(**cloud_args)
        self.password = password

    async def __aenter__(self):
        self.runner, self.base = await fake_cloud.start(self.cloud)
        self.session = aiohttp.ClientSession()
        self.client = hon.HonClient(fake_cloud.EMAIL, self.password, self.session, api_url=self.base)
        return self

    async def __aexit__(self, *exc):
        await self.session.close()
        await self.runner.cleanup()

    def mac(self, kind):
        return next(m for m, a in self.cloud.appliances.items() if a["info"]["applianceTypeName"] == kind)


async def wait_for(condition, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if condition():
            return True
        await asyncio.sleep(0.02)
    return False


# ── client ───────────────────────────────────────────────────────────────────

def test_login_and_read_everything():
    async def go():
        async with Env() as e:
            apps = await e.client.appliances()
            assert [a["applianceTypeName"] for a in apps] == ["WD", "DW", "IH"]
            dw = next(a for a in apps if a["applianceTypeName"] == "DW")
            assert "startProgram" in await e.client.commands(dw)
            assert model.flat_params(await e.client.context(dw))["machMode"] == "2"
            assert (await e.client.maintenance(dw))["tabs"] == 50
            assert (await e.client.statistics(dw))["programsCounter"] == 44
            assert await e.client.aws_token()
            assert e.cloud.logins == 1 and e.cloud.refreshes == 0     # one login for all the calls
    run(go())


def test_wrong_password_is_an_auth_error():
    async def go():
        async with Env(password="sbagliata") as e:
            with pytest.raises(hon.HonAuthError):
                await e.client.appliances()
    run(go())


def test_a_refused_password_is_not_tried_again():
    async def go():
        async with Env(password="sbagliata") as e:
            for _ in range(4):
                with pytest.raises(hon.HonAuthError):
                    await e.client.appliances()
            assert e.cloud.calls.count(("GET", "/ciam/authorize")) == 1 and e.client.auth_failed
    run(go())


def test_null_fields_from_the_cloud_do_not_break_the_requests():
    async def go():
        async with Env() as e:
            dw = next(a for a in await e.client.appliances() if a["applianceTypeName"] == "DW")
            assert "startProgram" in await e.client.commands({**dw, "code": None, "series": None, "fwVersion": None})
            e.cloud.appliances.clear()
            assert await e.client.appliances() == []
    run(go())


def test_unreachable_cloud_is_a_connection_error():
    async def go():
        async with aiohttp.ClientSession() as s:
            c = hon.HonClient("a@b.c", "x", s, api_url="http://127.0.0.1:9")
            with pytest.raises(hon.HonConnectionError):
                await c.login()
    run(go())


def test_expired_tokens_are_renewed_without_the_password():
    async def go():
        async with Env() as e:
            await e.client.appliances()
            e.cloud.expire_tokens()                      # the cloud now answers 403, like after 15 minutes
            assert len(await e.client.appliances()) == 3
            assert e.cloud.logins == 1 and e.cloud.refreshes == 1
    run(go())


def test_tokens_are_renewed_before_they_expire():
    async def go():
        async with Env(token_seconds=hon.TOKEN_MARGIN + 1) as e:
            await e.client.appliances()
            await asyncio.sleep(1.2)
            await e.client.appliances()
            assert e.cloud.logins == 1 and e.cloud.refreshes == 1
            assert ("POST", "/ciam/refresh") in e.cloud.calls
    run(go())


def test_dead_session_falls_back_to_a_full_login():
    async def go():
        async with Env() as e:
            await e.client.appliances()
            e.cloud.expire_tokens()
            e.cloud.fail_refresh = True
            assert len(await e.client.appliances()) == 3
            assert e.cloud.logins == 2
    run(go())


def test_a_cloud_that_keeps_refusing_does_not_cause_a_login_storm():
    async def go():
        async with Env() as e:
            await e.client.appliances()
            e.cloud._authorized = lambda request: False       # every call is refused, whatever the token
            for _ in range(5):
                with pytest.raises(hon.HonError):
                    await e.client.appliances()
            assert e.cloud.logins <= 2 and e.cloud.refreshes <= 1      # one renewal and one new login, then it waits
    run(go())


def test_parallel_requests_share_one_login():
    async def go():
        async with Env() as e:
            apps = await e.client.appliances()
            e.cloud.expire_tokens()
            await asyncio.gather(*(e.client.context(a) for a in apps))
            assert e.cloud.logins == 1 and e.cloud.refreshes == 1
    run(go())


def test_send_builds_the_same_request_as_the_app():
    async def go():
        async with Env() as e:
            dw = next(a for a in await e.client.appliances() if a["applianceTypeName"] == "DW")
            built = model.build_start(await e.client.commands(dw), "PROGRAMS.DW.ECO", {"delayTime": "60"})
            await e.client.send(dw, built["command"], built["parameters"], built["ancillary"], built["options"],
                                built["program_name"])
            body = e.cloud.sent[-1]
            assert body["commandName"] == "startProgram" and body["programName"] == "PROGRAMS.DW.ECO"
            assert body["parameters"]["delayTime"] == "60" and body["macAddress"] == dw["macAddress"]
            assert body["transactionId"].startswith(dw["macAddress"]) and body["device"]["mobileId"] == e.client.mobile_id
            assert body["applianceType"] == "DW" and body["attributes"]["channel"] == "mobileApp"
    run(go())


def test_translations():
    async def go():
        async with Env() as e:
            names = model.program_i18n(await e.client.translations("it"))
            assert names["PROGRAMS.DW.ECO"] == "Eco"
    run(go())


# ── mqtt codec ───────────────────────────────────────────────────────────────

def test_codec_handles_split_and_joined_packets():
    topic, payload = "haier/things/x/event/appliancestatus/update", b"x" * 300      # 2-byte length
    body = len(topic).to_bytes(2, "big") + topic.encode() + payload
    publish = bytes([0x30]) + realtime._varint(len(body)) + body
    stream = b"\xd0\x00" + publish + publish + b"\x90\x03\x00\x01\x00"
    buf, got = bytearray(), []
    for i in range(0, len(stream), 7):                 # arrives 7 bytes at a time
        buf.extend(stream[i:i + 7])
        got += realtime.split_packets(buf)
    assert [k for k, _, _ in got] == [realtime.PINGRESP, realtime.PUBLISH, realtime.PUBLISH, realtime.SUBACK]
    assert realtime.parse_publish(got[1][1], got[1][2]) == (topic, payload, 0, 0)
    assert not buf


def test_codec_packets():
    assert realtime.connect_packet("abc", 60) == b"\x10\x0f\x00\x04MQTT\x04\x02\x00\x3c\x00\x03abc"
    assert realtime.subscribe_packet(7, ["a/b"]) == b"\x82\x08\x00\x07\x00\x03a/b\x00"
    assert realtime.puback_packet(258) == b"\x40\x02\x01\x02"
    body = b"\x00\x01t\x00\x09hi"
    assert realtime.parse_publish(0x02, body) == ("t", b"hi", 1, 9)
    assert realtime._varint(321) == b"\xc1\x02"


def test_codec_refuses_truncated_packets():
    for flags, body in ((0, b""), (0, b"\x00"), (0, b"\x00\x09abc"), (2, b"\x00\x01t\x00")):
        with pytest.raises(ValueError):
            realtime.parse_publish(flags, body)


def test_status_parameters_accepts_both_shapes():
    assert realtime.status_parameters({"parameters": [{"parName": "machMode", "parNewVal": 2}, {"parName": "x"}]}) == {"machMode": "2"}
    assert realtime.status_parameters({"parameters": {"temp": {"parNewVal": "40"}, "y": "1"}}) == {"temp": "40", "y": "1"}
    assert realtime.status_parameters({}) == {} and realtime.status_parameters(None) == {}


# ── real-time channel ────────────────────────────────────────────────────────

class Listener:
    def __init__(self, env, apps, monkeypatch=None):
        self.status, self.presence, self.channel = [], [], []
        self.rt = realtime.HonRealtime(
            env.client, apps, url=env.base.replace("http", "ws") + "/mqtt",
            on_status=lambda mac, p: self.status.append((mac, p)),
            on_presence=lambda mac, up: self.presence.append((mac, up)),
            on_channel=self.channel.append)


def test_realtime_receives_status_and_presence(monkeypatch):
    async def go():
        async with Env() as e:
            apps = await e.client.appliances()
            lis = Listener(e, apps)
            lis.rt.start()
            assert await wait_for(lambda: lis.rt.connected)
            _, subscribed = e.cloud.sockets[0]
            assert len(subscribed) == 12                     # 4 topics each, in packets of at most 8
            mac = e.mac("DW")
            e.cloud.set_params(mac, {"machMode": "2", "remainingTimeMM": "120"})
            e.cloud.set_presence(e.mac("WD"), False)
            assert await wait_for(lambda: lis.status and lis.presence)
            assert lis.status == [(mac, {"machMode": "2", "remainingTimeMM": "120"})]
            assert lis.presence == [(e.mac("WD"), False)]
            await lis.rt.stop()
            assert lis.channel == [True, False] and not lis.rt.connected
            assert await wait_for(lambda: all(ws.closed for ws, _ in e.cloud.sockets))
    run(go())


def test_realtime_reconnects_with_fresh_tokens(monkeypatch):
    monkeypatch.setattr(realtime, "BACKOFF_MIN", 0.05)

    async def go():
        async with Env() as e:
            apps = await e.client.appliances()
            lis = Listener(e, apps)
            lis.rt.start()
            assert await wait_for(lambda: lis.rt.connected)
            e.cloud.id_tokens.clear()                        # the old id token is no longer accepted
            e.cloud.expire_tokens()
            await e.cloud.drop_sockets()
            assert await wait_for(lambda: lis.channel == [True, False, True])
            e.cloud.set_params(e.mac("WD"), {"prPhase": "3"})
            assert await wait_for(lambda: lis.status)
            await lis.rt.stop()
    run(go())


def test_realtime_is_not_up_if_every_subscription_is_refused(monkeypatch):
    monkeypatch.setattr(realtime, "BACKOFF_MIN", 0.05)

    async def go():
        async with Env() as e:
            apps = await e.client.appliances()
            e.cloud.refuse_subscriptions = True
            lis = Listener(e, apps)
            lis.rt.start()
            assert await wait_for(lambda: "refused" in lis.rt.last_error)
            assert not lis.rt.connected and lis.channel == []
            e.cloud.refuse_subscriptions = False             # the cloud accepts again: the listener recovers alone
            assert await wait_for(lambda: lis.rt.connected)
            await lis.rt.stop()
    run(go())


def test_realtime_stops_and_says_so_when_the_password_is_refused(monkeypatch):
    async def go():
        async with Env() as e:
            apps = await e.client.appliances()
            e.cloud.password = "cambiata"                    # changed in the app: sessions and tokens die
            e.cloud.session_ids.clear()
            e.cloud.expire_tokens()
            said = []
            rt = realtime.HonRealtime(e.client, apps, url=e.base.replace("http", "ws") + "/mqtt",
                                      on_status=lambda *a: None, on_presence=lambda *a: None,
                                      on_auth_failed=lambda: said.append(1))
            rt.start()
            assert await wait_for(lambda: said == [1])
            assert await wait_for(lambda: rt._task.done()) and not rt.connected
            await asyncio.sleep(0.2)
            assert e.cloud.calls.count(("GET", "/ciam/authorize")) == 2      # the first login and one failed try
            await rt.stop()
    run(go())


def test_realtime_keeps_pinging_while_the_server_talks(monkeypatch):
    monkeypatch.setattr(realtime, "PING_EVERY", 0.2)

    async def go():
        async with Env() as e:
            apps = await e.client.appliances()
            lis = Listener(e, apps)
            lis.rt.start()
            assert await wait_for(lambda: lis.rt.connected)
            ws, _ = e.cloud.sockets[0]
            pings, send = [], ws.send_bytes

            async def spy(data):
                if data == b"\xd0\x00":
                    pings.append(time.time())
                await send(data)
            ws.send_bytes = spy
            for _ in range(12):                              # the server never stops talking
                e.cloud.set_params(e.mac("DW"), {"remainingTimeMM": "1"})
                await asyncio.sleep(0.05)
            assert len(pings) >= 2, "the client must send PINGREQ even if it keeps receiving messages"
            await lis.rt.stop()
    run(go())


def test_realtime_survives_a_broken_callback_and_a_refused_login(monkeypatch):
    monkeypatch.setattr(realtime, "BACKOFF_MIN", 0.05)

    async def go():
        async with Env() as e:
            apps = await e.client.appliances()
            seen = []

            def bad(mac, params):
                seen.append(mac)
                raise RuntimeError("boom")
            rt = realtime.HonRealtime(e.client, apps, url=e.base.replace("http", "ws") + "/mqtt",
                                      on_status=bad, on_presence=lambda *a: None)
            rt.start()
            assert await wait_for(lambda: rt.connected)
            e.cloud.set_params(e.mac("DW"), {"a": "1"})
            e.cloud.set_params(e.mac("DW"), {"a": "2"})
            assert await wait_for(lambda: len(seen) == 2)
            assert rt.connected and rt.messages == 2
            await rt.stop()
    run(go())


# ── appliance state ──────────────────────────────────────────────────────────

def appliance(name, nick="Test", i18n=None) -> Appliance:
    info = {**load(name, "appliance_data"), "macAddress": "aa-bb-cc-00-00-01", "nickName": nick}
    a = Appliance(info)
    a.set_commands(load(name, "commands"), i18n or model.program_i18n(fake_cloud.I18N))
    a.apply_context(load(name, "attributes"))
    a.maintenance, a.statistics = load(name, "maintenance"), load(name, "statistics")
    return a


def test_washer_running_state():
    a = appliance("wd_209")
    assert a.type == "WD" and a.is_cycle and not a.is_hob and a.connected is True
    assert a.status == "running" and a.active and a.remaining == 163 and a.phase == "washing"
    assert a.program_name == "Eco 40-60"                     # prCode 42: the dial program, with its hOn name
    assert a.error_code is None
    assert abs(a.end_at - (time.time() + 163 * 60)) < 61 and a.end_at % 60 == 0
    assert a.counters == ["filterCleaning", "drumCleaning"] and a.maintenance_due is False
    assert set(a.resettable) == {"filterCleaning"}           # the drum counter cannot be reset by hand


def test_end_time_is_stable_while_the_minutes_tick():
    a = appliance("wd_209")
    first = a.end_at
    a.apply_live({"remainingTimeMM": "162"})                 # the machine counts down: same end, only jitter
    assert a.end_at == first
    a.apply_live({"remainingTimeMM": "140"})                 # the machine changed its estimate
    assert a.end_at < first
    a.apply_live({"machMode": "3"})
    assert a.status == "paused" and a.end_at is None and a.phase == "washing"
    a.apply_live({"machMode": "7"})
    assert a.status == "finished" and a.program_name is None and a.phase is None and not a.active


def test_end_time_does_not_drift_when_the_machine_does_not_count_down(monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(appliance_mod, "time", type("T", (), {"time": staticmethod(lambda: clock[0])}))
    a = appliance("wd_209")
    first = a.end_at
    for _ in range(4):                                       # read again every 3 minutes: always "163 minutes"
        clock[0] += 180
        a.apply_context(load("wd_209", "attributes"))
        assert a.end_at == first
    a.apply_live({"remainingTimeMM": "151"})                 # 12 minutes passed, 12 minutes less: same end
    assert a.end_at == first
    clock[0] += 60
    a.apply_live({"remainingTimeMM": "170"})                 # a real change of mind of the machine
    assert a.end_at > first


def test_machine_status_ignores_the_connection():
    a = appliance("wd_209")
    a.apply_presence(False)
    assert a.status == "offline" and a.machine_status == "running"


def test_pending_settings_expire_one_by_one(monkeypatch):
    clock = [1_800_000_000.0]
    monkeypatch.setattr(appliance_mod, "time", type("T", (), {"time": staticmethod(lambda: clock[0])}))
    a = appliance("dw_12658")
    a.set_pending({"waterHard": "6"})
    clock[0] += 60
    a.set_pending({"lightStatus": "1"})                      # a second change must not keep the first alive
    clock[0] += 40
    a.refresh()
    assert a.params["waterHard"] == "4" and a.params["lightStatus"] == "1"
    a.drop_pending(["lightStatus"])
    assert a.params["lightStatus"] == "0"


def test_scheduled_adds_the_delay():
    a = appliance("dw_12658")
    a.apply_live({"machMode": "5", "delayTime": "60", "remainingTimeMM": "100"})
    assert a.status == "scheduled" and a.remaining == 160 and a.phase == "scheduled"


def test_offline_and_error():
    a = appliance("dw_12658")
    assert a.status == "running" and a.phase == "prewash" and a.program_name == "Igienizzante"
    a.apply_live({"errors": "E2"})
    assert a.status == "error" and a.error_code == "E2"
    a.apply_live({"errors": "00"})
    a.apply_presence(False)
    assert a.status == "offline" and not a.active and a.program_name is None
    a.apply_live({"prPhase": "2"})                           # it talks again: it is online
    assert a.connected is True and a.status == "running"


def test_real_time_values_win_over_an_older_rest_answer():
    a = appliance("dw_12658")
    asked = time.time()
    time.sleep(0.01)
    a.apply_live({"remainingTimeMM": "99"})
    a.apply_presence(False)
    a.apply_context(load("dw_12658", "attributes"), asked)   # this answer was requested BEFORE the two events
    assert a.params["remainingTimeMM"] == "99" and a.connected is False
    a.apply_context(load("dw_12658", "attributes"))          # a new request: the cloud is authoritative again
    assert a.params["remainingTimeMM"] == "146" and a.connected is True


def test_an_empty_answer_does_not_wipe_the_state():
    a = appliance("dw_12658")
    a.apply_context({})                                      # the cloud sometimes answers with no payload
    assert a.status == "running" and a.params["remainingTimeMM"] == "146" and a.connected is True


def test_pending_settings_are_shown_until_the_machine_confirms(monkeypatch):
    a = appliance("dw_12658")
    assert a.params["waterHard"] == "4"
    a.set_pending({"waterHard": "6"})
    a.apply_context(load("dw_12658", "attributes"))          # the cloud still reports the old value
    assert a.params["waterHard"] == "6"
    a.apply_live({"waterHard": "5"})                         # the machine says something else: believe it
    assert a.params["waterHard"] == "5"
    a.set_pending({"lightStatus": "1"})
    monkeypatch.setattr(appliance_mod, "PENDING_TTL", 0)
    time.sleep(0.01)
    a.refresh()
    assert a.params["lightStatus"] == "0"                    # never confirmed: back to the real value


def test_program_started_from_an_app_is_recognised():
    a = appliance("wd_209")
    iot = next(p for p in a.programs if p["id"].startswith("iot_") and p["prCode"])
    a.apply_live({"prCode": str(iot["prCode"]), "prPosition": str(iot["fixed"].get("prPosition")), "machMode": "2"})
    dial = a.current_program
    assert dial is not None and not dial["id"].startswith("iot_")       # without an activity: the dial program
    a.activity = {"ttl": int(time.time()) + 600, "attributes": {"prCode": str(iot["prCode"]), "programName": iot["key"]}}
    assert a.current_program["key"] == iot["key"]
    a.activity["ttl"] = int(time.time()) - 5                 # old activity: ignored
    assert a.current_program["key"] == dial["key"]
    a.apply_live({"prCode": "0"})
    assert a.current_program is None


def test_a_program_started_from_here_is_named_before_the_cloud_says_so():
    a = appliance("wd_209")
    iot = next(p for p in a.programs if p["id"].startswith("iot_") and p["prCode"])
    a.activity = {}
    a.note_started(iot)
    a.apply_live({"prCode": str(iot["prCode"]), "prPosition": str(iot["fixed"].get("prPosition")), "machMode": "2"})
    assert a.program_name == iot["name"]


def test_a_cloud_answer_without_the_new_cycle_does_not_take_the_name_away():
    """Seconds after a start the cloud may answer with no activity yet: the name given here stays, for a while."""
    a = appliance("wd_209")
    iot = next(p for p in a.programs if p["id"].startswith("iot_") and p["prCode"])
    running = {"prCode": str(iot["prCode"]), "prPosition": str(iot["fixed"].get("prPosition")), "machMode": "2"}
    shadow = {"shadow": {"parameters": {k: {"parNewVal": v} for k, v in running.items()}}}
    a.note_started(iot)
    a.apply_context({**shadow, "activity": {}})
    assert a.program_name == iot["name"] and a.raw()["hon_activity_program"] == iot["id"]
    a.apply_context({**shadow, "activity": {"attributes": {"prCode": running["prCode"]}}})      # still no program name
    assert a.program_name == iot["name"]
    # the cloud names the cycle: its version wins from then on, also when it is empty again
    other = next(p for p in a.programs if p is not iot and str(p.get("prCode")) == running["prCode"])
    a.apply_context({**shadow, "activity": {"attributes": {"prCode": running["prCode"], "programName": other["key"]}}})
    assert a.current_program is other
    a.apply_context({**shadow, "activity": {}})
    assert a.activity == {}
    # and a start of long ago is forgotten
    a.note_started(iot)
    a._started = (time.time() - 3600, a._started[1])
    a.apply_context({**shadow, "activity": {}})
    assert a.activity == {} and a.raw()["hon_activity_program"] is None


def test_raw_parameters_tell_when_they_were_read():
    a = appliance("dw_12658")
    a.context_at = 0.0
    assert a.raw()["hon_read_at"] is None
    a.apply_context(json.loads((FIX / "dw_12658" / "attributes.json").read_text()))
    read_at = a.raw()["hon_read_at"]
    assert read_at.endswith("+00:00") and abs(time.time() - __import__("datetime").datetime.fromisoformat(read_at).timestamp()) < 5


def test_programs_that_only_start_from_the_machine_are_not_offered():
    a = appliance("dw_12658")
    local = a.programs[0]
    local["remote"] = False
    a._labels = a._make_labels()
    assert local not in a.program_labels().values() and len(a.program_labels()) == len(a.programs) - 1
    assert a.find_program(local["id"]) is local              # still known: it can be the one running


def test_hidden_programs_start_by_code_but_are_not_listed():
    """The app reaches some programs only from its guided choices: same here, by key or code."""
    a = appliance("wd_209")
    hidden = next(p for p in a.hidden_programs if p["id"] == "iot_dry_mixed_dry")
    assert hidden not in a.programs and hidden not in a.program_labels().values()
    assert a.find_program("iot_dry_mixed_dry") is hidden and a.find_program(hidden["key"].lower()) is hidden
    assert a.program(hidden["key"]) is hidden
    built = model.build_start(a.commands, hidden["key"], {})
    assert built["program_name"] == hidden["key"] and built["parameters"]["prCode"] == str(hidden["prCode"])
    # a listed program with the same name wins over a hidden one
    listed = a.programs[0]
    hidden["name"] = listed["name"]
    assert a.find_program(listed["name"]) is listed


def test_hidden_program_is_named_only_when_the_cloud_names_it():
    a = appliance("wd_209")
    hidden = next(p for p in a.hidden_programs if p["id"] == "iot_dry_mixed_dry")
    a.activity = {}
    a.apply_live({"prCode": str(hidden["prCode"]), "prPosition": str(hidden["fixed"].get("prPosition")), "machMode": "2"})
    by_code = a.current_program
    assert by_code is None or by_code in a.programs          # by its code alone: never the hidden one
    a.note_started(hidden)
    assert a.current_program is hidden and a.program_name == hidden["name"]
    a.activity["ttl"] = int(time.time()) - 5                 # old activity: ignored
    assert a.current_program is by_code


def test_find_program_and_labels():
    a = appliance("dw_12658")
    assert a.find_program("eco")["key"] == "PROGRAMS.DW.ECO"
    assert a.find_program("Programs.dw.Eco")["key"] == "PROGRAMS.DW.ECO"
    assert a.find_program(" Igienizzante ")["id"] == "hygiene"
    assert a.find_program("non esiste") is None and a.find_program("") is None
    labels = a.program_labels()
    assert len(labels) == len(a.programs) and all(a.find_program(label) is p for label, p in labels.items())


def test_draft_follows_the_rules_of_the_program():
    a = appliance("wd_209")
    a.select_program("PROGRAMS.WM_WD.CARE_30")
    assert a.option("dryOption") is not None and a.draft_values["dryOption"] == "0"
    assert a.option("dryLevel") is None                      # drying off: the level is not editable
    assert a.option("prCode") is None and a.option("nonEsiste") is None
    a.set_option("dryOption", "1")
    assert a.option("dryLevel")["type"] == "enum"
    default = next(d for d in a.program(a.draft_key)["params"] if d["key"] == "dryLevel")["default"]
    assert a.draft_values["dryLevel"] == default != "0"      # drying on: the default level, not "no drying"
    a.set_option("dryLevel", "3")
    a.set_option("temp", 40)
    opts = a.draft_options()
    assert opts["dryOption"] == "1" and opts["dryLevel"] == "3" and opts["temp"] == "40"
    built = model.build_start(a.commands, a.draft_key, opts)
    assert built["parameters"]["dryLevel"] == "3" and built["ancillary"]["dryOption"] == "1"
    a.set_option("dryOption", "0")                           # drying off again: the level goes back to "none"
    assert a.option("dryLevel") is None and "dryLevel" not in a.draft_options()
    assert model.build_start(a.commands, a.draft_key, a.draft_options())["parameters"]["dryLevel"] == "0"
    with pytest.raises(ValueError):
        a.set_option("temp", "95")
    with pytest.raises(ValueError):
        a.set_option("dryLevel", "3")
    with pytest.raises(ValueError):
        a.select_program("PROGRAMS.NON.ESISTE")
    a.select_program("PROGRAMS.WM_WD.ECO_40_60_NEW_ENERGY_LABEL")      # a new program starts from its defaults
    assert a.draft_values.get("dryOption", "0") == "0"


def test_timed_drying_is_optional_and_needs_drying_on():
    a = appliance("wd_209")
    a.select_program("PROGRAMS.WM_WD.CARE_30")
    assert a.option("dryTime") is None and "dryTime" not in a.draft_values      # drying off: no duration
    a.set_option("dryOption", "1")
    assert a.option("dryTime")["optional"] and "dryTime" not in a.draft_options()   # available, but not set
    assert "dryTime" not in model.build_start(a.commands, a.draft_key, a.draft_options())["parameters"]
    a.set_option("dryTime", 2)
    assert model.build_start(a.commands, a.draft_key, a.draft_options())["parameters"]["dryTime"] == "2"
    a.set_option("dryOption", "0")
    assert "dryTime" not in a.draft_options()
    assert "dryTime" not in model.build_start(a.commands, a.draft_key, a.draft_options())["parameters"]
    a.set_option("dryOption", "1")                           # drying on again: the old duration is forgotten
    assert "dryTime" not in a.draft_values and a.option("dryTime") is not None


def test_every_program_of_every_fixture_can_be_built_from_its_defaults():
    for name in ("wd_209", "dw_12658", "ih_654", "dw_509"):
        a = Appliance({"macAddress": "m", "applianceTypeName": name.split("_")[0].upper()})
        a.set_commands(load(name, "commands"))
        assert a.programs
        for p in a.programs:
            a.select_program(p["key"])
            built = model.build_start(a.commands, a.draft_key, a.draft_options())
            assert built["command"] == "startProgram" and built["parameters"]
            for key, value in a.draft_options().items():
                target = built["parameters"] if key in built["parameters"] else built["ancillary"]
                assert str(target[key]) == value


def test_option_kinds():
    a = appliance("wd_209")
    kinds = a.option_kinds()
    assert kinds["temp"] == {"select"} and kinds["delayTime"] == {"number"} and "switch" in kinds["dryOption"]
    assert "prCode" not in kinds and "lang" not in kinds
    assert kinds["dryLevel"] == {"select"} and kinds["steamLevel"] == {"select"}     # levels are never a switch
    assert param_kind({"type": "range", "min": 0, "max": 1}) == "switch"
    assert param_kind({"type": "enum", "values": ["1", "0"]}) == "switch"
    assert param_kind({"key": "dryLevel", "type": "enum", "values": ["0", "1"]}) == "select"
    assert param_kind({"key": "dirtyLevel", "type": "range", "min": 1, "max": 3, "step": 1}) == "select"
    assert param_values({"type": "range", "min": 1, "max": 3, "step": 1}) == ["1", "2", "3"]
    assert param_kind({"key": "delayTime", "type": "range", "min": 0, "max": 1410, "step": 30}) == "number"
    assert param_kind({"type": "fixed", "value": "1"}) is None


def test_hob_zones():
    a = appliance("ih_654")
    assert a.is_hob and a.zones == 4 and a.status == "offline"       # the fixture hob is switched off
    a.apply_presence(True)
    assert a.status == "off" and a.zone(1) == {"on": False, "power": 0, "pan": False, "hot": False, "temperature": 0.0, "timer": 0}
    a.apply_live({"onOffStatusZ2": "1", "powerZ2": "7", "panStatusZ2": "1", "hotStatusZ2": "1",
                  "remainingTimeHHZ2": "1", "remainingTimeMMZ2": "5"})
    assert a.status == "on" and a.zone(2) == {"on": True, "power": 7, "pan": True, "hot": True, "temperature": 0.0, "timer": 65}
    a.apply_presence(False)                                  # switched off: no stale "zone on" in Home Assistant
    assert a.status == "offline" and a.zone(2)["on"] is False and a.zone(2)["power"] == 0 and a.zone(2)["hot"] is None
    assert a.counters == [] and a.maintenance_due is None and a.settings[0]["key"] == "powerManagement"


def test_raw_parameters_for_templates():
    a = appliance("dw_12658")
    raw = a.raw()
    assert raw["machMode"] == "2" and raw["prPhase"] == "1" and raw["hon_connected"] is True
    assert raw["hon_last_program"] == "hygiene" and raw["hon_activity_program"] == "hygiene"
    hob = appliance("ih_654")
    hob.apply_presence(True)
    hob.apply_live({"tempZ2": "180"})
    assert hob.zone(2)["temperature"] == 180.0 and hob.raw()["tempZ2"] == "180"
    hob.apply_presence(False)
    assert hob.zone(2)["temperature"] is None and hob.raw()["hon_connected"] is False


def test_dishwasher_maintenance():
    a = appliance("dw_12658")
    assert a.counters == ["filterCleaning", "drumCleaning", "drawerCleaning", "sprayArmsCleaning"]
    assert a.maintenance_due is True                          # the filter is overdue in the fixture
    assert a.resettable == {"filterCleaning": "commands", "sprayArmsCleaning": "commands", "drawerCleaning": "commands"}
    assert [s["key"] for s in a.settings] and a.simple_commands
