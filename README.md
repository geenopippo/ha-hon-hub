# hOn Hub per Home Assistant

Integrazione personalizzata per gli elettrodomestici dell'app **hOn** (Candy, Hoover, Haier).
Parla direttamente con il cloud hOn, riceve gli aggiornamenti **in tempo reale** sullo stesso canale
dell'app ufficiale e porta in Home Assistant programmi, opzioni, impostazioni e manutenzione.

*English summary [below](#english).*

Sviluppata su Home Assistant 2026.10 per una lavasciuga, una lavastoviglie e un piano a induzione Candy; i comandi
che costruisce sono stati confrontati con quelli inviati dall'app ufficiale.
Nessuna libreria da installare: l'integrazione usa solo ciò che Home Assistant ha già.

## Cosa fa

- **Stato in diretta**: stato della macchina, programma, fase, tempo residuo, fine prevista. Gli aggiornamenti
  arrivano appena la macchina li manda (canale MQTT di hOn); un controllo periodico fa da rete di sicurezza.
- **Avvio dei programmi**: scegli il programma, regola le sue opzioni (temperatura, centrifuga, asciugatura,
  partenza ritardata…) e premi *Avvia programma*. Le opzioni seguono le regole del programma, come nell'app:
  per esempio il livello di asciugatura compare solo se attivi *Asciuga dopo il lavaggio*.
- **Pausa, riprendi, interrompi.**
- **Manutenzione**: cicli rimanenti prima di pulire filtro, cestello, cassetto e bracci irroratori; pulsanti per
  azzerare i contatori dopo la pulizia; sale e brillantante; pastiglie rimaste. I cicli di manutenzione
  (pulizia cesto, anticalcare, check-up) sono nell'elenco dei programmi.
- **Impostazioni della macchina**: durezza dell'acqua, livello brillantante, luce interna, avviso di fine ciclo,
  limite di potenza del piano.
- **Piano a induzione**: potenza e calore residuo di ogni zona, blocco tasti, limite di potenza
  (le ricette non si possono ancora avviare da qui).
- **Evento di ciclo** (avviato, in pausa, ripreso, finito, errore) per le automazioni.
- **Diagnostica** scaricabile, senza dati dell'account né indirizzi delle macchine.

## Installazione

### Con HACS
1. HACS → menu in alto a destra → **Repository personalizzati**.
2. Incolla `https://github.com/geenopippo/ha-hon-hub`, tipo **Integrazione**, e aggiungi.
3. Cerca **hOn Hub** in HACS, scaricala e riavvia Home Assistant.

### A mano
Copia la cartella `custom_components/hon_hub` dentro `/config/custom_components/` e riavvia Home Assistant.

### Collegamento dell'account
Impostazioni → Dispositivi e servizi → **Aggiungi integrazione** → *hOn Hub*, poi email e password dell'app hOn.
Ogni elettrodomestico dell'account diventa un dispositivo.

Può convivere con altre integrazioni hOn già installate: usa un dominio diverso (`hon_hub`).

## Avviare un programma

Dalla pagina del dispositivo: *Programma da avviare* → eventuali *Opzione: …* → **Avvia programma**.

Da un'automazione o uno script, con l'azione `hon_hub.start_program`:

```yaml
action: hon_hub.start_program
data:
  device_id: 0123456789abcdef            # il dispositivo hOn Hub
  program: "Eco 40-60"                   # nome, codice (eco_40_60) o chiave PROGRAMS.X.Y
  options:
    temp: 40
    spinSpeed: 1000
    delayTime: 120                       # minuti
```

Con `validate_only: true` il comando viene preparato e controllato ma **non inviato**: la risposta mostra
che cosa partirebbe. Utile per provare un'automazione senza far partire la macchina.

L'azione accetta anche i programmi che l'app non mette in elenco perché li propone solo dalle sue scelte
guidate o dai comandi vocali (per esempio `iot_wash_cotton_steam`): vanno indicati con il codice o con la
chiave. Nell'elenco *Programma da avviare* restano quelli che mostra l'app.

Per avviare da remoto la macchina deve essere accesa, collegata e con il **controllo remoto attivo** (si attiva
dal suo pannello). Se non lo è, l'integrazione non invia nulla e lo dice.

### Avviso a fine ciclo

```yaml
triggers:
  - trigger: state
    entity_id: event.lavastoviglie_ciclo
conditions:
  - condition: state
    entity_id: event.lavastoviglie_ciclo
    attribute: event_type
    state: finished
actions:
  - action: notify.notify
    data:
      message: "Lavastoviglie: {{ trigger.to_state.attributes.program }} finito"
```

## Cose da sapere

- **Serve Internet.** Le macchine hOn accettano comandi solo dal cloud: non hanno un'interfaccia locale.
- Quando una macchina è spenta risulta *Non connessa*; i contatori di manutenzione restano visibili,
  i comandi no.
- Email e password restano in Home Assistant, come per ogni integrazione; la password viene inviata a hOn solo
  al primo accesso e quando la sessione scade.
- Se qualcosa non torna: pagina del dispositivo → **Scarica la diagnostica**. Per un registro dettagliato:

  ```yaml
  logger:
    logs:
      custom_components.hon_hub: debug
  ```

## Sviluppo

```
python -m venv .venv && .venv/bin/pip install aiohttp pytest
.venv/bin/python -m pytest -q          # client, canale in tempo reale, stato e comandi su un cloud finto
tests/ha_test_env.sh                   # cloud hOn finto + Home Assistant di prova in Docker
.venv/bin/python tests/e2e_ha.py setup && (cd tests && ../.venv/bin/python e2e_scenario.py)   # scenario completo
python scripts/translations.py         # rigenera translations/en.json e it.json
```

Il nucleo (`hon.py`, `realtime.py`, `model.py`, `appliance.py`) non dipende da Home Assistant.
I dati di esempio in `tests/fixtures` vengono da [Andre0512/hon-test-data](https://github.com/Andre0512/hon-test-data) (MIT).

## English

Custom integration for the appliances of the **hOn** app (Candy, Hoover, Haier). It talks to the hOn cloud
directly, gets **real-time** updates on the same MQTT channel as the official app, and has no Python
requirements to install.

- State, program, phase, remaining time and expected end, pushed as the machine reports them.
- Program selection with its options as entities (they follow the rules of the program) and a start button;
  pause, resume, stop; `hon_hub.start_program` action with a `validate_only` mode that sends nothing. The action
  also takes, by code or key, the programs the app only offers from its guided choices.
- Maintenance counters with reset buttons, salt and rinse aid, tablets left; machine settings.
- Induction hobs: power and residual heat per zone.
- A cycle event entity (started, paused, resumed, finished, error) for automations; downloadable diagnostics.

Install it as a HACS custom repository (`https://github.com/geenopippo/ha-hon-hub`, type *Integration*) or copy
`custom_components/hon_hub` into your `custom_components` folder, restart, then add *hOn Hub* with the email
and password of the hOn app. Commands need the machine online with remote control enabled on its panel;
hOn appliances have no local API, so Internet access is required.

Developed on Home Assistant 2026.10 for a Candy washer dryer, dishwasher and induction hob; the commands it
builds were compared with the ones sent by the official app. Hob recipes cannot be started yet. Other hOn
appliance types show up with the basic entities only.

Not affiliated with Haier, Candy or Hoover. Thanks to [pyhOn](https://github.com/Andre0512/pyhOn) and
[gvigroux/hon](https://github.com/gvigroux/hon), which documented much of the hOn cloud. MIT license.
