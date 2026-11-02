# Airwise from iPhone — private, authenticated access

**Status (2026-11-02): prepared but NOT enabled.** The Mac mini's read-only
self-hosted probe found Airwise healthy on `127.0.0.1:8766` and did
**not** find a Tailscale CLI in the standard macOS locations.
No service is currently published, even inside a tailnet.

## Architecture

```text
iPhone Safari
   ⇅  HTTPS + signed-in Tailscale iOS app
Tailscale private tailnet (device/user grants)
   ⇅  Tailscale Serve (tailnet only; NEVER Funnel)
Mac mini 127.0.0.1:18767
   Airwise identity-checking gateway:
     expected .ts.net Host + exact Tailscale-User-Login
     GET allowlist; POST disabled by default
   ⇅  loopback, sanitized Host, no authentication-header passthrough
Mac mini 127.0.0.1:8766
   existing Airwise and local SQLite
```

The sidecar `src/home_air_quality_alexa/iphone_gateway.py` is optional,
stdlib-only and **not started automatically**. It only binds to
`127.0.0.1:18767`. The main Airwise service remains unchanged and
locally bound to `127.0.0.1:8766`. There is no remote font, telemetry,
public Internet dashboard, or GitHub upload.

### Explicit trust boundaries

- Tailscale Serve is the trusted identity-asserting reverse proxy:
  `Tailscale-User-Login` is injected by Serve after it strips untrusted
  copies; the gateway **does not** trust regular HTTP `X-Forwarded-*`
  or cookies. Tailscale traffic from tagged machines lacks a user
  login and is denied. A logged-in, allowlisted user must also satisfy
  the tailnet's grants / network ACLs.
- The gateway requires the exact Mac `*.ts.net` name and the exact
  allowed Tailscale login, configured **locally**. It never derives
  these from private repository files or IP geolocation.
- Only a restricted set of GET endpoints is available.
  POST requests are **denied by default**. An explicit
  `--enable-writes` flag can permit only the three supported JSON POST
  routes, after matching the HTTPS browser Origin and the tailnet
  identity. Leave this **disabled** for the first iPhone usability audit.
- Same-Mac malicious local processes could forge identity headers on
  `127.0.0.1:18767`. Protecting against local process compromise is
  outside this gateway's threat model; these processes already run
  where private SQLite and Amazon sessions exist.
- Do not enable Funnel, `0.0.0.0`, router port forwarding, unauthenticated
  Cloudflare tunnels, or log private headers/data.
- Do not overwrite a previously configured Tailscale Serve endpoint
  on port 443 for another project.

## Step 1 — prerequisites requiring user action

1. Install the official [Tailscale for macOS](https://tailscale.com/docs/install/mac)
   app on the Mac mini. Complete the VPN extension permission and sign
   in with your own account. This may require admin approval on the Mac.
2. Install Tailscale for iOS from the App Store and sign in to the
   **same tailnet**, enabling its VPN profile. Restrict allowed devices
   in Tailscale admin-console grants/ACLs; prefer the specific user or
   device identity over permitting all tailnet members.
3. Enable HTTPS certificates / Serve in the Tailscale admin flow if
   prompted. This may require a tailnet admin and an explicit
   web-based consent step.
4. Check the exact Mac hostname and Tailscale user identity privately.
   You can get these from the Mac's Tailscale UI/CLI; **never paste
   credentials, node keys or private network settings into GitHub**.

The CLI for the App Store macOS variant is typically:
`/Applications/Tailscale.app/Contents/MacOS/Tailscale`.
For standalone CLI integration it may be
`/usr/local/bin/tailscale`. On the App Store variant, set
`TAILSCALE_BE_CLI=1` when scripting.

## Step 2 — start the private gateway, read-only

From the **checked-out updated source branch** on the Mac:

```bash
cd ~/Projects/home-air-quality-alexa
git switch research/alexa-history-probe
git pull --ff-only
.venv/bin/python -m pip install -e .

# Replace the placeholders locally using your own exact Tailscale values:
.venv/bin/python -m home_air_quality_alexa.iphone_gateway \
  --tailnet-host '<exact-mac-host>.ts.net' \
  --allowed-login '<your-tailscale-login>'
```

This command does **not** turn on Serve: it only runs a loopback HTTP
gateway while this terminal stays open. It has no elevated privileges.
The login is an identity value, not a secret, but keep it in local
commands/configuration rather than public CI or commit history.

Validate the running gateway without revealing your own identity;
its endpoint must return 403 when Tailscale identity is absent:

```bash
curl --noproxy '*' -sS -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:18767/
# Expected: 403
```

## Step 3 — Serve via the private tailnet only

**Before enabling**, verify `tailscale status`, active tailnet
authorization rules, and `tailscale serve status`. Do not overwrite
an unrelated existing Serve mapping.

Example for standalone CLI integration:

```bash
tailscale status
tailscale serve status
tailscale serve --bg --https=443 http://127.0.0.1:18767
tailscale serve status
```

For the App Store CLI, replace `tailscale` with
`TAILSCALE_BE_CLI=1 /Applications/Tailscale.app/Contents/MacOS/Tailscale`.

Tailscale Serve's HTTPS address will be a Mac-specific
`https://<exact-mac-host>.ts.net/` URL. On the signed-in iPhone,
open **that HTTPS URL** in Safari (NOT
`http://127.0.0.1:8766`, which points to the iPhone).

If the Tailscale app requires sign-in, certificate provisioning or
Serve approval, complete that interactively. Never automate bypasses.

### Disable safely

To disable this **specific** Serve listener, use:

```bash
tailscale serve --https=443 off
```

Only do so when the port 443 mapping belongs to Airwise; do not reset
the whole Tailscale node or another project's endpoints. Stop the
separate `iphone_gateway` Python process with Ctrl-C. This does not
stop the original Airwise port 8766.

## Step 4 — finish the real mobile tests

Use [iPhone + VoiceOver checklist](iphone-voiceover-checklist.md).
Check portrait/landscape, text scaling, Safari zoom, VoiceOver rotor,
and the data tables. In read-only mode, forms can be inspected visually,
but journal and outdoor POST submissions are intentionally blocked.
Do not claim form submissions work on the iPhone until a separate
opt-in write-enabled security review is completed.

## Tests and references

```bash
python3 -m unittest discover -s tests -v
python3 scripts/check_mobile_ui.py
```

The `tests/test_iphone_gateway.py` suite uses localhost fake servers
only and covers missing/wrong identity, Host manipulation,
non-allowlisted routes, default-denied POST, CSRF and optional opt-in
writes. It tests our gateway's logic, **not** whether Tailscale is
installed, authenticated or correctly granted on the Mac/iPhone.

Documentation:
- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve):
  private tailnet scope, identity headers and ACLs.
- [Tailscale Serve CLI](https://tailscale.com/docs/reference/tailscale-cli/serve):
  `--bg`, HTTPS proxy, status and disable commands.
- [macOS CLI](https://tailscale.com/docs/reference/tailscale-cli?tab=macos):
  installed binary variants.
