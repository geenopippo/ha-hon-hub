#!/usr/bin/env python3
"""Writes custom_components/hon_hub/translations/{en,it}.json.

The names of options and settings are repeated for three platforms (switch, number, select) and two roles:
keeping them here in one table avoids two thousand lines of hand-written json. Run it after editing.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components" / "hon_hub"))
import const  # noqa: E402  (no Home Assistant imports in there)

# parameter (translation key) -> (english, italian)
PARAMS = {
    "temp": ("Temperature", "Temperatura"),
    "spin_speed": ("Spin speed", "Centrifuga"),
    "dry_level": ("Drying level", "Livello di asciugatura"),
    "dry_time": ("Drying time", "Durata asciugatura"),
    "dry_option": ("Dry after washing", "Asciuga dopo il lavaggio"),
    "dirty_level": ("Soil level", "Livello di sporco"),
    "steam_level": ("Steam", "Vapore"),
    "delay_time": ("Delayed start", "Partenza ritardata"),
    "extra_rinse_1": ("Extra rinse 1", "Risciacquo extra 1"),
    "extra_rinse_2": ("Extra rinse 2", "Risciacquo extra 2"),
    "extra_rinse_3": ("Extra rinse 3", "Risciacquo extra 3"),
    "acquaplus": ("Aquaplus", "Acqua Plus"),
    "good_night": ("Good Night (quiet)", "Good Night (silenzioso)"),
    "prewash": ("Prewash", "Prelavaggio"),
    "hygiene": ("Hygiene", "Igiene"),
    "anticrease": ("Anti-crease", "Antipiega"),
    "auto_detergent": ("Automatic detergent dosing", "Dosaggio automatico detersivo"),
    "auto_softener": ("Automatic softener dosing", "Dosaggio automatico ammorbidente"),
    "water_hardness": ("Water hardness", "Durezza dell'acqua"),
    "extra_dry": ("Extra dry", "Extra asciutto"),
    "open_door": ("Door opening at the end", "Apertura porta a fine ciclo"),
    "half_load": ("Half load", "Mezzo carico"),
    "eco_express": ("Eco Express", "Eco Express"),
    "three_in_one": ("3 in 1", "3 in 1"),
    "add_dish": ("Add dish", "Aggiungi stoviglie"),
    "tab_status": ("Tablet detergent", "Detersivo in pastiglie"),
    "intensive": ("Intensive", "Intensivo"),
    "diverter_level": ("Basket", "Cesto"),
    "light": ("Interior light", "Luce interna"),
    "buzzer_disabled": ("Mute end-of-cycle sound", "Disattiva avviso di fine ciclo"),
    "last_cycle_memo": ("Remember last cycle", "Memo ultimo ciclo"),
    "rinse_aid_level": ("Rinse aid level", "Livello brillantante"),
    "power_management": ("Power limit", "Limite di potenza"),
    "night_wash": ("Night wash", "Lavaggio notturno"),
    "energy_saving": ("Energy saving", "Risparmio energetico"),
    "sterilization": ("Sterilization", "Sterilizzazione"),
    "rinse_iterations": ("Number of rinses", "Numero risciacqui"),
    "main_wash_time": ("Main wash time", "Durata lavaggio"),
    "key_lock": ("Key lock", "Blocco tasti"),
    "zone_map": ("Zone", "Zona"),
    "auto_dose": ("Automatic dosing", "Dosaggio automatico"),
}
assert set(PARAMS) == set(const.KNOWN_PARAMS.values()), set(PARAMS) ^ set(const.KNOWN_PARAMS.values())

# values of the multiple-choice parameters -> (english, italian)
VALUES = {
    "dry_level": {"0": ("No drying", "Nessuna"), "1": ("Iron dry", "Pronto stiro"), "2": ("Light", "Leggera"),
                  "3": ("Cupboard dry", "Pronto armadio"), "4": ("Extra dry", "Extra asciutto"),
                  "11": ("No drying", "Nessuna"), "12": ("Iron dry", "Pronto stiro"),
                  "13": ("Cupboard dry", "Pronto armadio"), "14": ("Ready to wear", "Pronto da indossare"),
                  "15": ("Extra dry", "Extra asciutto")},
    "dirty_level": {"1": ("Light", "Poco"), "2": ("Normal", "Normale"), "3": ("Heavy", "Molto")},
    "steam_level": {"0": ("No", "No"), "1": ("Cotton", "Cotone"), "2": ("Delicates", "Delicati"),
                    "3": ("Synthetics", "Sintetici")},
    "diverter_level": {"0": ("Both", "Entrambi"), "1": ("Upper", "Superiore"), "2": ("Lower", "Inferiore")},
    "temp": {"0": ("Cold", "Freddo"), **{str(t): (f"{t} °C", f"{t} °C") for t in (20, 30, 40, 50, 60, 65, 70, 75, 80, 90)}},
    "spin_speed": {"0": ("No spin", "No centrifuga"),
                   **{str(s): (f"{s} rpm", f"{s} giri") for s in (400, 600, 800, 1000, 1200, 1400, 1500, 1600)}},
}

OPTION_PREFIX = ("Option: ", "Opzione: ")

STATUS = {"ready": ("Ready", "Pronta"), "running": ("Running", "In funzione"), "paused": ("Paused", "In pausa"),
          "scheduled": ("Scheduled", "Programmata"), "error": ("Error", "Errore"), "finished": ("Finished", "Finito"),
          "test": ("Test", "Test"), "stopping": ("Stopping", "In arresto"), "offline": ("Offline", "Non connessa")}
HOB = {"on": ("On", "Acceso"), "off": ("Off", "Spento"), "offline": ("Offline", "Non connesso")}
PHASES = {"washing": ("Washing", "Lavaggio"), "spin": ("Spin", "Centrifuga"), "rinse": ("Rinse", "Risciacquo"),
          "drying": ("Drying", "Asciugatura"), "steam": ("Steam", "Vapore"), "weighting": ("Weighing", "Pesatura"),
          "scheduled": ("Scheduled", "Programmato"), "tumbling": ("Anti-crease", "Antipiega"),
          "refresh": ("Refresh", "Rinfresca"), "heating": ("Heating", "Riscaldamento"),
          "prewash": ("Prewash", "Prelavaggio"), "finished": ("Finished", "Finito"),
          "hot_rinse": ("Hot rinse", "Risciacquo caldo"), "heat_stroke": ("Heating up", "Riscaldamento"),
          "cooldown": ("Cool down", "Raffreddamento")}
assert set(STATUS) == set(const.STATES) and set(HOB) == set(const.STATES_HOB) and set(PHASES) == set(const.PHASES)
DRY = {"no_dry": ("No drying", "Nessuna"), "iron_dry": ("Iron dry", "Pronto stiro"), "no_dry_iron": ("Light", "Leggera"),
       "cupboard_dry": ("Cupboard dry", "Pronto armadio"), "extra_dry": ("Extra dry", "Extra asciutto"),
       "ready_to_wear": ("Ready to wear", "Pronto da indossare")}
assert set(DRY) == set(const.DRY_LEVELS.values())
EVENTS = {"started": ("Started", "Avviato"), "paused": ("Paused", "In pausa"), "resumed": ("Resumed", "Ripreso"),
          "finished": ("Finished", "Finito"), "error": ("Error", "Errore"), "scheduled": ("Scheduled", "Programmato")}
assert set(EVENTS) == set(const.CYCLE_EVENTS)
COUNTERS = {"filter_cleaning": ("Filter cleaning", "Pulizia filtro"), "drum_cleaning": ("Drum cleaning", "Pulizia cestello"),
            "drawer_cleaning": ("Detergent drawer cleaning", "Pulizia cassetto detersivo"),
            "spray_arms_cleaning": ("Spray arms cleaning", "Pulizia bracci irroratori")}
CYCLES = ("cycles", "cicli")

# everything else: path -> (english, italian)
TEXTS = {
    "config.step.user.title": ("hOn account", "Account hOn"),
    "config.step.user.description": (
        "Enter the email and password you use in the hOn app. Home Assistant talks to the hOn cloud directly.",
        "Inserisci l'email e la password che usi nell'app hOn. Home Assistant parla direttamente con il cloud hOn."),
    "config.step.user.data.email": ("Email", "Email"),
    "config.step.user.data.password": ("Password", "Password"),
    "config.step.reauth_confirm.title": ("hOn account", "Account hOn"),
    "config.step.reauth_confirm.description": (
        "The hOn login is no longer valid: enter email and password again.",
        "L'accesso a hOn non è più valido: inserisci di nuovo email e password."),
    "config.step.reauth_confirm.data.email": ("Email", "Email"),
    "config.step.reauth_confirm.data.password": ("Password", "Password"),
    "config.error.invalid_auth": ("Wrong email or password", "Email o password errate"),
    "config.error.cannot_connect": ("The hOn cloud cannot be reached", "Il cloud hOn non è raggiungibile"),
    "config.error.no_appliances": ("This hOn account has no appliances", "Questo account hOn non ha elettrodomestici"),
    "config.error.unknown": ("Unexpected answer from hOn: check the log", "Risposta inattesa da hOn: controlla il registro"),
    "config.abort.already_configured": ("This account is already configured", "Questo account è già configurato"),
    "config.abort.reauth_successful": ("Login updated", "Accesso aggiornato"),

    "device.washer_dryer.name": ("Washer dryer", "Lavasciuga"),
    "device.washing_machine.name": ("Washing machine", "Lavatrice"),
    "device.tumble_dryer.name": ("Tumble dryer", "Asciugatrice"),
    "device.dishwasher.name": ("Dishwasher", "Lavastoviglie"),
    "device.induction_hob.name": ("Induction hob", "Piano a induzione"),
    "device.oven.name": ("Oven", "Forno"),
    "device.hood.name": ("Hood", "Cappa"),
    "device.fridge.name": ("Fridge", "Frigorifero"),
    "device.wine_cellar.name": ("Wine cellar", "Cantinetta"),

    "entity.sensor.status.name": ("Status", "Stato"),
    "entity.sensor.hob_status.name": ("Status", "Stato"),
    "entity.sensor.program.name": ("Program", "Programma"),
    "entity.sensor.phase.name": ("Phase", "Fase"),
    "entity.sensor.remaining_time.name": ("Remaining time", "Tempo residuo"),
    "entity.sensor.end_time.name": ("Expected end", "Fine prevista"),
    "entity.sensor.cycle_water.name": ("Cycle water", "Acqua del ciclo"),
    "entity.sensor.cycle_energy.name": ("Cycle energy", "Energia del ciclo"),
    "entity.sensor.total_cycles.name": ("Total cycles", "Cicli totali"),
    "entity.sensor.total_cycles.unit_of_measurement": CYCLES,
    "entity.sensor.temperature.name": ("Set temperature", "Temperatura impostata"),
    "entity.sensor.spin_speed.name": ("Set spin speed", "Centrifuga impostata"),
    "entity.sensor.dry_level.name": ("Set drying level", "Asciugatura impostata"),
    "entity.sensor.error_code.name": ("Error code", "Codice errore"),
    "entity.sensor.tabs.name": ("Tablets left", "Pastiglie rimaste"),
    "entity.sensor.zone_power.name": ("Zone {zone} power", "Zona {zone} potenza"),

    "entity.binary_sensor.connected.name": ("Connection", "Connessione"),
    "entity.binary_sensor.remote_control.name": ("Remote control", "Controllo remoto"),
    "entity.binary_sensor.door.name": ("Door", "Porta"),
    "entity.binary_sensor.salt.name": ("Salt low", "Sale in esaurimento"),
    "entity.binary_sensor.rinse_aid.name": ("Rinse aid low", "Brillantante in esaurimento"),
    "entity.binary_sensor.detergent.name": ("Detergent low", "Detersivo in esaurimento"),
    "entity.binary_sensor.softener.name": ("Softener low", "Ammorbidente in esaurimento"),
    "entity.binary_sensor.key_lock.name": ("Key lock", "Blocco tasti"),
    "entity.binary_sensor.maintenance.name": ("Maintenance due", "Manutenzione da fare"),
    "entity.binary_sensor.zone_hot.name": ("Zone {zone} residual heat", "Zona {zone} calore residuo"),

    "entity.button.start.name": ("Start program", "Avvia programma"),
    "entity.button.pause.name": ("Pause", "Pausa"),
    "entity.button.resume.name": ("Resume", "Riprendi"),
    "entity.button.stop.name": ("Stop", "Interrompi"),

    "entity.select.program.name": ("Program to start", "Programma da avviare"),
    "entity.number.tabs.name": ("Set tablets", "Imposta pastiglie"),
    "entity.event.cycle.name": ("Cycle", "Ciclo"),

    "exceptions.offline.message": ("The appliance is not connected: switch it on and try again.",
                                   "L'elettrodomestico non è connesso: accendilo e riprova."),
    "exceptions.remote_control_off.message": (
        "Remote control is off on the machine: turn it on from its panel and try again.",
        "Il controllo remoto è disattivato sulla macchina: attivalo dal pannello e riprova."),
    "exceptions.invalid_auth.message": ("The hOn login is no longer valid: enter email and password again.",
                                        "L'accesso a hOn non è più valido: inserisci di nuovo email e password."),
    "exceptions.cannot_connect.message": ("The hOn cloud cannot be reached.", "Il cloud hOn non è raggiungibile."),
    "exceptions.command_rejected.message": ("hOn rejected the command.", "hOn ha rifiutato il comando."),
    "exceptions.cloud_error.message": ("hOn error: {error}", "Errore di hOn: {error}"),
    "exceptions.invalid_option.message": ("Not valid: {error}", "Non valido: {error}"),
    "exceptions.hob_not_supported.message": ("Hob recipes cannot be started from Home Assistant yet.",
                                             "Le ricette del piano non si possono ancora avviare da Home Assistant."),
    "exceptions.no_program.message": ("Choose the program in \"Program to start\" first.",
                                      "Scegli prima il programma in \"Programma da avviare\"."),
    "exceptions.no_device.message": ("This is not an appliance of hOn Hub, or the integration is not loaded.",
                                     "Non è un elettrodomestico di hOn Hub, oppure l'integrazione non è caricata."),
    "exceptions.program_not_found.message": ("Program \"{program}\" not found on this appliance.",
                                             "Programma \"{program}\" non trovato su questo elettrodomestico."),

    "services.start_program.name": ("Start program", "Avvia programma"),
    "services.start_program.description": (
        "Starts a program on a hOn appliance, with optional settings. Options left out keep the default of the program.",
        "Avvia un programma su un elettrodomestico hOn, con le opzioni facoltative. Le opzioni non indicate restano "
        "quelle predefinite del programma."),
    "services.start_program.fields.device_id.name": ("Appliance", "Elettrodomestico"),
    "services.start_program.fields.device_id.description": ("The appliance to start.", "L'elettrodomestico da avviare."),
    "services.start_program.fields.program.name": ("Program", "Programma"),
    "services.start_program.fields.program.description": (
        "Name as shown in \"Program to start\" (for example Eco 40-60), its code (eco_40_60) or its key (PROGRAMS.X.Y).",
        "Nome come in \"Programma da avviare\" (per esempio Eco 40-60), codice (eco_40_60) o chiave (PROGRAMS.X.Y)."),
    "services.start_program.fields.options.name": ("Options", "Opzioni"),
    "services.start_program.fields.options.description": (
        "Options of the program with their hOn name, for example temp, spinSpeed, delayTime (minutes), dryOption, dryLevel.",
        "Opzioni del programma con il loro nome hOn, per esempio temp, spinSpeed, delayTime (minuti), dryOption, dryLevel."),
    "services.start_program.fields.validate_only.name": ("Check only", "Solo verifica"),
    "services.start_program.fields.validate_only.description": (
        "Build and check the command without sending it: the answer shows what would be sent.",
        "Prepara e controlla il comando senza inviarlo: la risposta mostra che cosa verrebbe inviato."),
}


def build(lang: int) -> dict:
    out: dict = {}

    def put(path: str, text: str) -> None:
        node = out
        *parents, leaf = path.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = text

    for path, texts in TEXTS.items():
        put(path, texts[lang])
    for key, texts in STATUS.items():
        put(f"entity.sensor.status.state.{key}", texts[lang])
    for key, texts in HOB.items():
        put(f"entity.sensor.hob_status.state.{key}", texts[lang])
    for key, texts in PHASES.items():
        put(f"entity.sensor.phase.state.{key}", texts[lang])
    for key, texts in DRY.items():
        put(f"entity.sensor.dry_level.state.{key}", texts[lang])
    for key, texts in EVENTS.items():
        put(f"entity.event.cycle.state_attributes.event_type.state.{key}", texts[lang])
    left = (": cycles left", ": cicli rimanenti")[lang]
    reset = ("Reset ", "Azzera ")[lang]
    for key, texts in COUNTERS.items():
        put(f"entity.sensor.{key}.name", texts[lang] + left)
        put(f"entity.sensor.{key}.unit_of_measurement", CYCLES[lang])
        put(f"entity.button.reset_{key}.name", reset + texts[lang][0].lower() + texts[lang][1:])
    for key, texts in PARAMS.items():
        for platform in ("switch", "number", "select"):
            put(f"entity.{platform}.option_{key}.name", OPTION_PREFIX[lang] + texts[lang])
            put(f"entity.{platform}.setting_{key}.name", texts[lang])
        for value, labels in VALUES.get(key, {}).items():
            put(f"entity.select.option_{key}.state.{value}", labels[lang])
            put(f"entity.select.setting_{key}.state.{value}", labels[lang])
    return out


if __name__ == "__main__":
    folder = ROOT / "custom_components" / "hon_hub" / "translations"
    folder.mkdir(exist_ok=True)
    for n, lang in enumerate(("en", "it")):
        (folder / f"{lang}.json").write_text(json.dumps(build(n), ensure_ascii=False, indent=2) + "\n")
        print("written", folder / f"{lang}.json")
