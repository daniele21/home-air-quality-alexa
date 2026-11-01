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

The private `airQualityHistory` API is **not** yet verified for returning usable
measurements with this Amazon account. To test it against your already paired
Amazon Air Quality Monitor, run:

```bash
cd ~/Projects/home-air-quality-alexa
git switch research/alexa-history-probe
git pull
.venv/bin/python -m pip install -e .
.venv/bin/air-quality-alexa history-fetch --sensor PM25 --days 7 --resolution ONE_HOUR
```

This makes **one history GraphQL request**, for **one sensor** and a bounded
seven-day window, after reading device metadata. It uses your existing
private login session: never share `amazon-login.json`. The collector passes
its actual Alexa `endpointId`, sensor `interfaceName`, and optional
`instance` from the device's observed metadata, never guessed values.

`--sensor` supports `PM25`, `PM10`, `VOC`, `CO`, `Humidity`,
`Air Quality`, or `temperature` (only if the device exposes the key).
`--resolution` allows `FIVE_MINS`, `ONE_HOUR`, `ONE_DAY`.
`--days` allows 1 to 31 per request. To request a different sensor, invoke
it separately; be considerate of Amazon rate limits.

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
A successful response containing zero rows is **not** proof the monitor has
no history — the interface/instance combination or time range might differ
from what the Alexa app requests. The implementation deliberately avoids
guessing alternative parameters or sending batches of speculative calls.

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
