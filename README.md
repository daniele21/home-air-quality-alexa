# Home Air Quality Alexa

Minimal Python collector for the **Amazon Smart Air Quality Monitor**. No Home Assistant, server or third-party dashboard required. Data stays on your Mac in SQLite and can be exported to CSV.

> **Experimental:** Amazon provides no public consumer readings API. Uses the third-party [`aioamazondevices`](https://github.com/chemelli74/aioamazondevices) library (pinned to **17.2.3**), which can break when Amazon changes authentication or device discovery. A successful account/device test is still required.

## Requirements

- macOS / Linux, Python 3.12+
- Amazon Smart Air Quality Monitor registered on an Alexa account; the experimental browser authorization can handle the sign-in method Amazon offers in your browser
- Internet access from the Mac to Amazon services

## Install

```bash
 git clone https://github.com/daniele21/home-air-quality-alexa.git
 cd home-air-quality-alexa
 python3 -m venv .venv
 .venv/bin/python -m pip install -e .
```

## First authentication (locally, never in chat or GitHub)

**Recommended for passwordless accounts (experimental):**

```bash
export ALEXA_EMAIL='your-amazon-account@example.com'
.venv/bin/air-quality-alexa login-browser --marketplace it
```

The command opens the **official Amazon** page `https://www.amazon.it/code`. Enter the short code shown in the Terminal, then sign in with the method offered by Amazon (passwordless, passkey, or other account login). The command polls Amazon until your approval is processed or the code expires, then stores the Alexa registration locally. **Do not enter the private device code, credentials, or session tokens into any chat or GitHub issue.** The public code shown in the Terminal is short-lived.

**Important:** this is an undocumented Amazon identity API. The device-code approach has been demonstrated by other projects using Audible device identities, **not yet confirmed to work with the Alexa iOS device identity** used by `aioamazondevices` (`A2IVLV5VM2W81`). Amazon may reject creation or registration of the code pair. If it does, report only the safe error status (HTTP code and error category), never response bodies or private tokens. The existing login remains unchanged.

**Fallback for accounts with a separate Amazon password and authenticator OTP:**

```bash
export ALEXA_EMAIL='your-amazon-account@example.com'
.venv/bin/air-quality-alexa login
```

Only the **legacy `login`** command requests an Amazon password and current authenticator OTP; neither is saved. The **`login-browser`** command asks for neither. A reusable, sensitive Amazon login session is saved to `~/.config/home-air-quality-alexa/amazon-login.json` (0600); the containing directory is private (0700). Protect this file as a password. Do **not** commit, upload or sync it to Drive.

```bash
.venv/bin/air-quality-alexa devices
.venv/bin/air-quality-alexa collect --once
.venv/bin/air-quality-alexa collect --interval 300
```

`devices` prints names/serials and available sensor keys. `collect --once` tests an initial snapshot (not live push updates). Keep `collect` running for ongoing collection: the library needs an **HTTP/2 push connection** for fresh environmental sensors, not only REST polling. Ctrl-C stops the collector.

For one specific monitor add `--device SERIAL`. Important: options `--email`, `--session-file` and `--db` go **before** the subcommand.

```bash
.venv/bin/air-quality-alexa export --output ~/air-quality.csv
```

Database: `~/.local/share/home-air-quality-alexa/readings.sqlite3`. Each reading includes device timestamp if provided, first-observed timestamp, sensor name, value and unit. Historical Alexa measurements **before first collection cannot be assumed available**. Only the reported value is saved, without speculative conversion or health interpretation.

## Reliability and privacy

- Keep polling at **300 seconds or longer** to reduce Amazon rate limiting.
- On session expiry or push-stream failure, the collector exits rather than silently storing stale data. Run `login-browser` (or legacy `login`) again if authentication expires.
- SQLite uses a unique key on `(device, sensor, sample time)` to avoid duplicating repeated device samples. If Amazon does not provide sample time, every observed poll can represent a separate reading and is marked `timestamp_source=poll`.
- `collection_events` captures success/failure status; no Amazon tokens or raw responses are written to the database.
- The raw Alexa library can still emit sensitive debug logs; do not enable its DEBUG logging or upload log bundles without checking them.
- If Amazon stops exposing the monitor, `devices` and `collect` report this rather than fabricating measurements.


## Historical data: experimental API discovery

Amazon's Alexa app shows past trends but neither the existing `aioamazondevices`
API nor `alexa-exporter` documents a working historical sensor download.
The `history` endpoint in `aioamazondevices` is for **voice interactions**,
not for air quality.

This research branch adds a **read-only metadata-only** diagnostic:

```bash
git switch research/alexa-history-probe
git pull
.venv/bin/python -m pip install -e .
.venv/bin/air-quality-alexa history-probe
```

**Confirmed on 2026-11-01 (real device/account):** `history-probe` successfully
read the Amazon Nexus GraphQL schema (54 root fields). It discovered:
`airQualityHistory` and `getPropertySceneHistoricalDataUrl`. This is
**real metadata evidence**, not yet proof of reading historical values.

The next experimental step is to inspect the signatures and output types:

```bash
git switch research/alexa-history-probe
git pull
.venv/bin/python -m pip install -e .
.venv/bin/air-quality-alexa history-inspect
```

`history-inspect` performs at most two additional read-only GraphQL metadata
requests, printing only argument names/types and return-type field names/types
for the two known operations. It does **not** call the history operations,
download measurements, expose session secrets or write to SQLite.

Please share only the printed JSON (after reviewing it for any identifiers).
The next code change will use the **actual argument signatures**, not guessed
parameters, to request a small time range for the known monitor.

The command uses your existing local Amazon login session and submits exactly
**one** GraphQL introspection query to the same Nexus host used by normal device
discovery. It outputs only candidate root query field names (no measurements,
tokens, device IDs, or response bodies). It neither guesses URLs, modifies
Alexa settings, nor imports historical data.

Interpretation:

- `metadata_available` with relevant `candidates`: leads to investigate; **not** proof
  that the API can return measurements or that you have access to it.
- `metadata_available` with empty candidates: this specific GraphQL root
  does not advertise obvious historical APIs; other APIs remain possible.
- `inconclusive`: introspection is disabled, unsupported or the request
  failed; **not** evidence that history is unavailable.

Never post your local `amazon-login.json` or DEBUG HTTP logs. The printed
result of `history-probe` is designed to be shareable.

If no endpoint is discoverable, the next credible route is to inspect the
network calls generated by opening historical charts in the Alexa mobile app
using a locally controlled debugging proxy. Some app traffic may be
certificate-pinned, so this also may not be possible without app-level access.

## Fetch Alexa air-quality history (live experimental query)

The API signature was confirmed by the user's authenticated Nexus GraphQL schema
on **2026-11-01**. The full input/response schema is also published publicly:

- [alexactl schema](https://github.com/Fishwaldo/alexactl/blob/main/internal/alexagraphql/schema/schema.graphql)
- [go-alexa schema](https://github.com/portpowered/go-alexa/blob/main/pkg/schemas/graphql/snapshots/live-schema.graphql)

The private `airQualityHistory` API has now returned a real seven-day hourly
PM2.5 series for the linked monitor; importing it with the validated
interface mapping still needs a fresh run on your Mac. Run:

```bash
cd ~/Projects/home-air-quality-alexa
git switch research/alexa-history-probe
git pull
.venv/bin/python -m pip install -e .
.venv/bin/air-quality-alexa history-fetch --sensor PM25 --days 7 --resolution ONE_HOUR
```

**Confirmed on the user's real monitor (2026-11-01):**
`history-fetch --sensor PM25 --days 7 --resolution ONE_HOUR`
returned **169** aggregated slots, **168 valid hourly PM2.5 values**, **one
missing slot**, and **168 newly inserted SQLite rows**. The response included
`average_value: 15.0`; this is Amazon's response value, not an independently
recomputed average. The original rolling query returned timestamps ending in
`:47:17.226`, because its start/end times followed the command invocation.

**Reproducible time windows:** to prevent successive downloads generating
different time offsets, `--days` now uses a stable UTC boundary matching the
selected resolution: five-minute, hourly or daily. Both boundaries are UTC.
You can also request **fixed calendar windows** with `--start` and `--end`
(YYYY-MM-DD, UTC, inclusive start / exclusive end). Up to 31 days per call:

```bash
# An older full month, non-overlapping with the October 25-31 sample
.venv/bin/air-quality-alexa history-fetch \\
  --sensor PM25 --resolution ONE_HOUR \\
  --start 2026-09-01 --end 2026-10-01

# Example: same dates, another air-quality sensor
.venv/bin/air-quality-alexa history-fetch \\
  --sensor PM10 --resolution ONE_HOUR \\
  --start 2026-09-01 --end 2026-10-01
```

**Data-quality caveat:** the 168 original points remain stored with their
`:47:17` rolling-window timestamps. A new aligned query that overlaps
those days will produce *different aggregation windows*, and the exact
timestamp primary key cannot merge semantically overlapping bins. Do not
combine or sum overlapping historical rows across different query origins
as independent observations. For now, prefer **non-overlapping date ranges**
for backfill and keep the initial data intact. Migration/reconciliation of
the first rolling window should be a separate reviewed change, not a silent
timestamp rewrite.

**Observed September result (2026-11-01):** a one-month hourly request
(`2026-09-01` through `2026-10-01`, end exclusive) returned 721
time buckets, **all 721 without endpoint data**, and 0 readings inserted.
This is an empty response skeleton, **not 721 zero PM2.5 measurements**.
The server's `averageValue: 0.0` is not a meaningful average with
no valid points. The CLI now reports `status: no_data` and
`average_value: null` for such responses. No `historical_readings` table
is created if there are no usable measurements.

**One-day probes confirmed on 2026-11-01:** both October 30 and October 20
return a valid historical series for PM2.5, so a strict seven-day retention
limit is ruled out. Each one-day hourly request returned **25 timestamps**
because Alexa includes the exclusive end timestamp at midnight. The importer
now enforces `start <= timestamp < end`, accepts at most the **24 hourly
buckets inside a full UTC day**, and reports the final point separately as
`outside_window_buckets: 1`. It does not treat that extra boundary as a
missing sample or an invalid measurement. `window_average_value` is computed
from accepted in-window values; `average_value` is Alexa's own value, which
may use different endpoint inclusion rules.

**Next retention test:** request one day in September before attempting a
multi-day backfill:

```bash
.venv/bin/air-quality-alexa history-fetch --sensor PM25 \\
  --resolution ONE_HOUR --start 2026-09-30 --end 2026-10-01 \\
  --probe-only
```

If this returns no data, compare one intermediate day in October (for example
October 10) to narrow a possible retention boundary, without concluding that
Amazon always keeps exactly a fixed number of days.

To distinguish a history retention limit from requests that span too much
time, use **one-day, read-only probes** (one GraphQL request each), without
altering your existing SQLite database:

```bash
# Control: a day inside the previously successful seven-day range
.venv/bin/air-quality-alexa history-fetch --sensor PM25 \\
  --resolution ONE_HOUR --start 2026-10-30 --end 2026-10-31 \\
  --probe-only

# Test: a day older than the seven-day range
.venv/bin/air-quality-alexa history-fetch --sensor PM25 \\
  --resolution ONE_HOUR --start 2026-10-20 --end 2026-10-21 \\
  --probe-only
```

If the first returns data and the second returns `no_data`, a retention
cutoff becomes more plausible. The month-long empty response alone cannot
prove a seven-day retention policy. Wait between probes to avoid excessive
Amazon requests. A `probe_only` result with `status: ok` returns
`would_save` but always reports `new_saved: 0`. Neither probe opens the
SQLite file.

Older history availability is unknown; one-month requests may return gaps
or less history than requested. Do not infer a retention period from the
success of the seven-day request.

This makes **one history GraphQL request**, for **one sensor** and a bounded
seven-day window, after reading device metadata. It uses your existing
private login session: never share `amazon-login.json`. The collector passes
its actual Alexa `endpointId`, sensor `interfaceName`, and optional
`instance` from the device's observed metadata, never guessed values.

`--sensor` supports `PM25`, `PM10`, `VOC`, `CO`, `Humidity`,
`Air Quality`, or `temperature` (only if the device exposes the key).
`--resolution` allows `FIVE_MINS`, `ONE_HOUR`, `ONE_DAY`.
`--days` allows 1 to 31 per request. `--start` and `--end` allow
a reproducible UTC date range and override `--days`. To request a different
sensor, invoke it separately; be considerate of Amazon rate limits.

Results are saved to `historical_readings` in the **same SQLite file** as
live `readings`, but with a separate primary key including device, sensor,
resolution, and timestamp. Repeated downloads cannot duplicate an existing
historical bucket; **live readings are never changed**. Rows with empty
`endpointIds` are treated as gaps, not zero-valued measurements.

Inspect the imported history:

```bash
sqlite3 ~/.local/share/home-air-quality-alexa/readings.sqlite3 \\
  'SELECT sensor_key,resolution,bucket_timestamp_utc,numeric_value,unit FROM historical_readings ORDER BY bucket_timestamp_utc DESC LIMIT 15;'
```

If Amazon rejects the request, the CLI prints a safe error category; response
bodies, authentication tokens, and endpoint identifiers are not printed.

**Verified interface mapping (live PM2.5 test on 2026-11-01):** Alexa returned
`interfaceName: "Alexa.RangeController"`, `instance: "6"`, while
`aioamazondevices==17.2.3` represents that same sensor using feature `range`,
instance `6`. The library's pinned AQM sensor mapping identifies PM2.5 as
range instance `6` (humidity=4, VOC=5, PM2.5=6, PM10=7, CO=8, air quality=9).
The importer now recognizes **only** this explicit, grounded interface alias
when both the exact sensor instance **and the requested device endpoint**
match. Other interface aliases or a mismatched instance are refused. The
initial real response contained 169 hourly rows: 168 valid values for the
monitor and one empty-endpoint gap. The updated mapping is exercised by
168-point offline regression tests, but the actual import requires rerunning
`history-fetch` on the Mac.

**If you get `status: identity_mismatch`:** the returned interface/instance
still does not match a validated mapping. The command prints only safe
identifiers, count summaries, and `new_saved: 0`. It never saves readings for
an unverified identity. Share that JSON without sharing credentials.

A successful response containing zero rows is **not** proof the monitor has
no history — the interface/instance combination or time range might differ
from what the Alexa app requests. The implementation deliberately avoids
guessing alternative parameters or sending batches of speculative calls.

## Resumable five-channel historical backfill

**Observed cutoff for this monitor on 2026-11-01:** October 1 returned
`no_data`; October 2, 10, 20 and 30 each returned 24 in-window hourly
PM2.5 measurements. This is consistent with approximately 30 days of
retention, but not evidence of an official Amazon policy or other
monitor/sensor behavior.

The `history-backfill` command performs a controlled, sequential import for
**five live-verified Alexa channels**: PM2.5, VOC, CO, humidity and the Air Quality index.
PM10 is listed in the community library's feature metadata, but the real Amazon
history GraphQL endpoint rejected PM10 on 2026-11-01, so it is intentionally
excluded from automated backfills. The Amazon device's published particulate
sensor is PM2.5, not a PM10 measurement.
It defaults to the last **30 completed UTC days**, one day per request,
with five seconds between requests. For a complete 30-day window with all
five verified channels, the plan contains **150 API requests**. No concurrent/burst
requests are sent. To reduce risk, the default caps each execution at
**30 requests**, after which rerunning the same command resumes at the next
uncompleted window.

**1. Preview the complete plan (no historical requests, no SQLite writes):**

```bash
cd ~/Projects/home-air-quality-alexa
git switch research/alexa-history-probe
git pull --ff-only
.venv/bin/python -m pip install -e .

.venv/bin/air-quality-alexa history-backfill \\
  --start 2026-10-02 --end 2026-11-01
```

**2. Apply a first small batch for the five channels, October 2-4 (15 requests):**

```bash
.venv/bin/air-quality-alexa history-backfill \\
  --start 2026-10-02 --end 2026-10-05 \\
  --max-requests 15 --delay 5 --apply
```

**3. After checking the results, complete the remaining days:**

```bash
.venv/bin/air-quality-alexa history-backfill \\
  --start 2026-10-02 --end 2026-11-01 \\
  --max-requests 30 --delay 5 --apply
```

Repeat the third command until `status: ok` and
`remaining_requests: 0`. A completed or confirmed-empty window is
skipped on subsequent runs. Failures and unexpected sensor identity
mismatches **stop the batch immediately** instead of continuing to send
requests. Progress checkpoints go to `historical_backfill_windows` in the
same private SQLite database. Nothing is sent to any external service
other than the existing authenticated Alexa API.

Options: `--sensor PM25 --sensor VOC` to select particular sensors;
`--device SERIAL` if multiple air monitors are present; `--chunk-days 7`
for fewer, longer requests after verifying that range length is supported;
`--force` to requery the selected windows including ones previously marked
complete/empty. The `--apply` flag is always required to send historical
requests.

**Important duplicate-series distinction:** the first 168 historical
PM2.5 readings were collected with rolling timestamps ending in
`:47:17`. These records remain untouched, but their hour-long
aggregation windows overlap hourly windows anchored at `:00`.
`historical_backfill_windows` tracks completed UTC ranges.
`historical_hourly_utc` is a **database view of hour-aligned readings only**,
so it excludes the old offset data and avoids double counting the two
different query alignments. Query it for downstream hourly charts:

```bash
sqlite3 ~/.local/share/home-air-quality-alexa/readings.sqlite3 \\
  "SELECT sensor_key, COUNT(*), MIN(bucket_timestamp_utc),
    MAX(bucket_timestamp_utc)
    FROM historical_hourly_utc GROUP BY sensor_key ORDER BY sensor_key;"
```

This is an import system, **not a scheduled service yet**. The
`Empty JSON data received` library warning has been nonfatal so far;
if a batch stops or data quality deteriorates, inspect safe counters and
stop rather than increasing request frequency. Avoid posting Amazon
cookies, device endpoint IDs or raw HTTP logs.

**Self-hosted Mac verification (2026-11-01):** a central private GitHub
Actions job successfully imported 24 UTC-hour readings for each of PM2.5,
VOC, CO, humidity and Air Quality score for October 30. The PM10 history
query failed with a redacted GraphQL error, after which the batch stopped.
The original Oct 2 PM2.5 one-day probe succeeded earlier on Nov 1 but a
later query returned only empty endpoint buckets, consistent with a sliding
retention limit. Do not assume all calendar days within a past month remain
queryable.

**Completed live backfill on 2026-11-01:** the private Mac mini runner
executed 25 bounded GraphQL history requests, using seven-day UTC windows
for Oct 3 through Oct 31 and five-second pauses. The five verified
channels each now have **696 distinct aligned hourly measurements (29 days)**.
A separate read-only Mac runner SQLite audit checked every expected UTC
hour and reported **zero gaps, zero invalid numerical values**, while
confirming that the legacy PM2.5 rolling-window records remain preserved.
The five aligned series total **3,480 measurements**. No Amazon session,
raw HTTP response, device identifier or detailed measurement logs were
uploaded as GitHub artifacts.

- [Backfill GitHub Actions run](https://github.com/daniele21/local-runner-infra/actions/runs/41352050336)
- [Independent read-only SQLite coverage audit](https://github.com/daniele21/local-runner-infra/actions/runs/41352633678)

Temperature is an independently exposed hardware sensor but was not
part of this five-channel historical backfill: its GraphQL history
mapping has not yet been verified.

## Airwise: dashboard locale dello storico

`src/home_air_quality_alexa/dashboard.py` è un server **solo localhost**
realizzato con la libreria standard Python; `dashboard.html` è una UI
responsive senza CDN, librerie JS, font esterni o raccolta di analytics.
I dati restano nel database SQLite del Mac, aperto esclusivamente in
**lettura** (`mode=ro` e `PRAGMA query_only`).

Caratteristiche: selezione sensore, trend orario reale con gap espliciti,
medie giornaliere, sovrapposizione del periodo precedente, min/max,
copertura in ore, vista giornaliera e navigazione tra periodi di 7, 14 o
30 giorni. Le medie dei periodi sono confrontate solo se **entrambi**
raggiungono almeno il 50% di copertura; altrimenti il confronto indica
`n.d.`. Tutte le date e le ore sono marcate **UTC** (nessuna
ambiguità nei cambi d'ora ottobre/marzo).

Si usano **solo** le letture storiche orarie allineate a inizio ora.
Le prime 168 letture PM2.5 con offset `:47` restano preservate ma non
entrano nei grafici; non si mescolano mai sensori con unità diverse.
La finestra recente è ancorata al giorno dell'ultima lettura oraria
presente, non alla data del browser, per non far sparire dati vecchi.

Avvio manuale sul Mac:

```bash
cd ~/Projects/home-air-quality-alexa
git switch research/alexa-history-probe
git pull --ff-only
.venv/bin/python -m pip install -e .
.venv/bin/python -m home_air_quality_alexa.dashboard --port 8766
```

Aprire dal **Mac mini**: http://127.0.0.1:8766/
L'API di sola lettura è `/api/dashboard?days=7&offset=0`
(`days=7|14|30`, `offset` in giorni da 0 a 90).

È previsto anche un workflow centrale self-hosted **separato** per
installare la stessa dashboard sotto un LaunchAgent macOS, dopo
validazione del commit sorgente e del database sul runner. Nessun
workflow automatico del collector viene creato da questa funzionalità.
`127.0.0.1` è **accessibile solo dalla macchina**: l'accesso dallo
smartphone richiede una configurazione privata separata, per esempio
Tailscale Serve con verifica esplicita delle regole d'accesso. Non
aprire la porta al pubblico o impostare `0.0.0.0`.

Il pannello è **descrittivo**: i valori e gli indici Alexa non sono
confrontati con limiti sanitari, soglie di sicurezza o scale che non
sono state verificate per questi sensori.

### Analisi approfondite degli inquinanti

La nuova pagina [`/pollution`](http://127.0.0.1:8766/pollution)
offre tre sezioni:

- **Episodi PM2.5**: mediana, 90° percentile empirico, picchi di
  almeno 2 ore consecutive sopra il percentile, intensità/durata ed
  eccesso cumulato relativo alla soglia statistica; una lacuna oraria
  interrompe l'episodio, non viene interpolata.
- **Distribuzioni temporali**: profilo sulle 24 ore, medie per giorno
  della settimana e heatmap 7×24 calcolata da vere letture abbinate
  a giorno e ora nel fuso Europe/Rome. I cambi d'ora sono gestiti
  senza eliminare né duplicare le misurazioni UTC.
- **Associazioni**: correlazioni contemporanee di Pearson fra PM2.5
  e VOC/CO/umidità sulle sole ore presenti in entrambi i canali,
  disponibili con ≥48 coppie non costanti. La correlazione non
  consente conclusioni di causalità; Air Quality index, essendo
  derivato, non è trattato come un sensore indipendente.

Per il PM2.5 è visualizzato **esclusivamente come riferimento**
il valore OMS 2021 per la media di 24h di particolato fine
**15 µg/m³ per aria ambientale**. Il confronto è disponibile
solo se la giornata ha tutte le **24 misurazioni orarie UTC**.
Non è una certificazione delle misure Amazon indoor né
una soglia di allarme individuale. Nei giorni incompleti
si mostra la media delle letture osservate ma non la classificazione.

Il dispositivo Amazon è un sensore consumer di qualità dell'aria
interna; le sue letture non sono complete per tutte le sostanze e
il canale CO **non sostituisce un allarme CO certificato**.

Riferimenti:
- [WHO Global Air Quality Guidelines (2021)](https://www.who.int/news-room/questions-and-answers/item/who-global-air-quality-guidelines)
- [US EPA: Indoor Particulate Matter](https://www.epa.gov/indoor-air-quality-iaq/sources-indoor-particulate-matter-pm)
- [US EPA: Low-Cost Air Pollution Monitors](https://www.epa.gov/indoor-air-quality-iaq/low-cost-air-pollution-monitors-and-indoor-air-quality)
- [Amazon: Using Your Smart Air Quality Monitor](https://digprjsurvey.amazon.co.uk/csad/help/node/G373E3KMG3DN2MX9)

### Diario domestico e confronto outdoor (solo con consenso esplicito)

La nuova vista locale `http://127.0.0.1:8766/context`, raggiungibile
anche dalla pagina **Analisi inquinamento**, separa due fonti:

1. **Annotazioni manuali**: cucina, finestre aperte, pulizie,
   candele/incenso, ventilazione, riscaldamento e altro. Ogni evento
   contiene data/ora, durata e una nota facoltativa. Le annotazioni
   possono essere eliminate e sono conservate in
   `~/.local/share/home-air-quality-alexa/context.sqlite3`
   (file locale, permessi 0600), **separato** dal database originale
   delle misurazioni Alexa.
2. **PM2.5 esterno modellato**: premendo esplicitamente
   `Scarica le stime PM2.5 outdoor` e inserendo latitudine/longitudine
   approssimate, il Mac interroga **solo**
   `https://air-quality-api.open-meteo.com/v1/air-quality`.
   Le coordinate sono arrotondate a **2 cifre decimali** prima dell'invio
   e salvate solo nel database locale. Nessuna posizione è dedotta
   automaticamente dal browser o dall'account Amazon.

Open-Meteo aggrega dati **Copernicus CAMS** su celle modellistiche
(europeo circa 11 km; globale più grossolano): NON sono misure di una
centralina davanti alla casa. Le misure Amazon e le stime CAMS vengono
abbinate **solo sulle stesse ore UTC**, senza interpolare mancanze;
sono mostrate medie delle ore condivise e differenza descrittiva.
In aggiunta, sul grafico vengono visualizzati i momenti annotati
delle attività, mentre un semplice delta prima/dopo mostra due ore
precedenti contro le due successive, ove tutte presenti. L'intervento
non può essere attribuito causalmente da questi soli dati.

Il browser contatta soltanto il server Mac sulla porta 8766; la
richiesta Open-Meteo viene emessa **dal backend solo su azione
esplicita**. L'API delle annotazioni richiede POST JSON
**dalla stessa Origin** e verifica l'header Host, con un corpo limitato;
la vista è accessibile solo su loopback. Non si creano connessioni
in uscita con il semplice caricamento della dashboard. Esiste un
limite locale di 15 minuti tra sincronizzazioni outdoor.

Per cancellare soltanto il diario e le stime outdoor, arresta Airwise
prima di eliminare il file `context.sqlite3`; il database
`readings.sqlite3` non viene toccato. Non pubblicare in GitHub
le coordinate, le note sulle attività o la sessione Amazon.

**Attribuzione:** [Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api)
e [Copernicus Atmosphere Monitoring Service (CAMS)](https://atmosphere.copernicus.eu/).
Verifica i termini della fonte per eventuali utilizzi commerciali.

## Airwise design skills: mobile-first UI governance

The repository owns **four original Agent Skills**, created after reviewing
popular public GitHub design-skill projects. They live under `.agents/skills/`
and are connected by [AGENTS.md](AGENTS.md) and a shared
[DESIGN.md](DESIGN.md).

| Original skill | Responsibility |
| --- | --- |
| [airwise-design-direction](.agents/skills/airwise-design-direction/SKILL.md) | Product-specific visual identity, hierarchy and semantic tokens |
| [airwise-mobile-first](.agents/skills/airwise-mobile-first/SKILL.md) | Responsive thumb navigation, 44px actions, forms, safe areas |
| [airwise-data-viz](.agents/skills/airwise-data-viz/SKILL.md) | Honest PM2.5/VOС/CO charts, units, model vs measurement |
| [airwise-visual-qa](.agents/skills/airwise-visual-qa/SKILL.md) | Audit → critique → harden → polish with explicit evidence |

Research, links, license notes and the rationale for which ideas were
*conceptually merged*: [skill-sources.md](docs/design/skill-sources.md).
First source-code review with P1 findings and delivery phases:
[audit-2026-11-02.md](docs/design/audit-2026-11-02.md).

### First implementation pass

All three Airwise pages now share consistent, thumb-friendly navigation.
On mobile the three destinations are displayed as a fixed bottom bar with
safe-area padding. Touch targets are 44px or larger where enforced by
the shared mobile rules, and the diary's inputs are at least 16px.
The pollution 24-hour heatmap scrolls within its own region instead of
compressing all cells to a narrow phone screen. Hourly, daily and
indoor/outdoor visualizations have adjacent semantic HTML data tables;
their canvas tooltips also respond to pointer-down/tap.

Offline structural gate:

```bash
python3 scripts/check_mobile_ui.py
python3 -m unittest discover -s tests -v
```

**A passing static gate does not replace actual mobile screenshots,
focus/touch testing or screen reader testing.** Viewport matrix:
320/375/390/768/1024/1440 px, 200% zoom, keyboard and reduced motion.
The visual QA skill defines an explicit **PARTIAL** status until those
rendered tests are run.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

No Amazon credentials are required for unit tests. **Live API testing must be done on your Mac** using your private login.

## Sources

- [Home Assistant Alexa Devices integration](https://www.home-assistant.io/integrations/alexa_devices) (supported sensors, rate limiting)
- [`aioamazondevices` library](https://github.com/chemelli74/aioamazondevices) (HTTP/2 push, login/session API)
- [Historical `alexa-exporter`](https://github.com/knyar/alexa-exporter) (old private Phoenix endpoints; archived)

## Browser-login diagnostics

- `Could not connect`: verify connectivity/DNS to `api.amazon.it`. No secrets are printed.
- `declined device-code creation (HTTP 400/403)`: this Amazon client/marketplace may not support the code-pair flow. Do not retry rapidly.
- `rejected device-code registration`: account approval failed or Amazon does not permit this device type.
- `expired`: restart `login-browser` and enter the **new** public code promptly.
- `registered ... incompatible`: Amazon accepted the pairing, but did not provide the full Alexa session required by the library. Do not attempt to splice tokens from another app.
- `session initialization failed`: Amazon returned a registration but the library could not complete cookies/capabilities; a successful device reading is still unverified.

This collector does not automate a Google sign-in. Google authentication (if offered) happens on Amazon's browser page, never inside the Python collector.
