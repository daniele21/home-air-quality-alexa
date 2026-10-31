# Home Air Quality Alexa

Minimal Python collector for the **Amazon Smart Air Quality Monitor**. No Home Assistant, server or third-party dashboard required. Data stays on your Mac in SQLite and can be exported to CSV.

> **Experimental:** Amazon provides no public consumer readings API. Uses the third-party [`aioamazondevices`](https://github.com/chemelli74/aioamazondevices) library (pinned to **17.2.3**), which can break when Amazon changes authentication or device discovery. A successful account/device test is still required.

## Requirements

- macOS / Linux, Python 3.12+
- Amazon Alexa account with authenticator-app 2FA enabled, and the Amazon Smart Air Quality Monitor registered
- Internet access from the Mac to Amazon services

## Install

```bash
 git clone https://github.com/daniele21/home-air-quality-alexa.git
 cd home-air-quality-alexa
 python3 -m venv .venv
 .venv/bin/python -m pip install -e .
```

## First authentication (locally, never in chat or GitHub)

```bash
export ALEXA_EMAIL='your-amazon-account@example.com'
.venv/bin/air-quality-alexa login
```

Enter the Amazon password and **current authenticator OTP** when prompted. The password and OTP are not saved. A reusable, sensitive Amazon login session is saved to `~/.config/home-air-quality-alexa/amazon-login.json` (0600); the containing directory is private (0700). Protect this file as a password. Do **not** commit, upload or sync it to Drive.

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
- On session expiry or push-stream failure, the collector exits rather than silently storing stale data. Run `login` again if authentication expires.
- SQLite uses a unique key on `(device, sensor, sample time)` to avoid duplicating repeated device samples. If Amazon does not provide sample time, every observed poll can represent a separate reading and is marked `timestamp_source=poll`.
- `collection_events` captures success/failure status; no Amazon tokens or raw responses are written to the database.
- The raw Alexa library can still emit sensitive debug logs; do not enable its DEBUG logging or upload log bundles without checking them.
- If Amazon stops exposing the monitor, `devices` and `collect` report this rather than fabricating measurements.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

No Amazon credentials are required for unit tests. **Live API testing must be done on your Mac** using your private login.

## Sources

- [Home Assistant Alexa Devices integration](https://www.home-assistant.io/integrations/alexa_devices) (supported sensors, rate limiting)
- [`aioamazondevices` library](https://github.com/chemelli74/aioamazondevices) (HTTP/2 push, login/session API)
- [Historical `alexa-exporter`](https://github.com/knyar/alexa-exporter) (old private Phoenix endpoints; archived)
