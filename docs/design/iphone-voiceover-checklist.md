# Airwise — physical iPhone & VoiceOver acceptance protocol

Status: **NOT YET EXECUTED** (requires an actual iPhone and a private,
explicitly approved access path to the Mac's local-only Airwise server).
Browser automation on a remote Mac does not imply iPhone/Safari or
screen-reader usability. Do not mark these checks complete from Chrome CDP.

## Preparation — protect the user's data

- Airwise binds to **127.0.0.1:8766**. That address points to the iPhone
  itself when entered on an iPhone; it is *not* the Mac mini. Don't suggest
  that entering the localhost URL in Mobile Safari works remotely.
- Do **not** change the bind address to `0.0.0.0`, forward router ports,
  expose the Mac via a public tunnel or disable Host/Origin checks to
  make testing easier. Private access requires a separate approved,
  authenticated, encrypted pathway and a security review.
- Use the synthetic-only fixture for screenshots or any export. Never
  upload real measurement data, activity diary content, precise
  coordinates, browser cookies or API logs to GitHub.
- Don't request the user to share private screenshots if an approved
  local testing workflow can run on-device instead.

## iPhone manual checklist (Safari)

Record device family, iOS/Safari major version, orientation, and access
method. Mark each check PASS / FAIL / NOT RUN; attach only synthetic
screenshots, stored privately.

1. Portrait and landscape: all three tabs reachable, selected tab
   announced and visible; bottom bar neither overlaps graph/form nor
   intersects home-indicator safe area.
2. At smallest enabled text size and with larger accessibility text:
   no clipping, unreadable labels or body-level horizontal scroll;
   scrolling a rail doesn't inadvertently scroll the entire page.
3. Safari text scaling and magnification: buttons stay identifiable;
   date selector and previous/next buttons remain reachable; no focus trap.
4. Each chart: touch near a data point to reveal its label; drag page
   vertically without being captured by the chart. For exact readings,
   expand the adjacent HTML data table and scroll it *locally*.
5. Pollution heatmap: use a finger to scroll horizontally; open
   `Heatmap in tabella` to read actual hourly averages and sample sizes.
   Empty/zero observations must be distinguished.
6. Context page: log an event with a harmless synthetic note, observe
   success and delete the event; test input keyboard types and return
   from the keyboard without obscuring the submission button. This step
   must not be run against private real data without explicit permission.
7. Outdoor opt-in: before pressing the sync button, loading the page
   must not request external coordinates, data or location. Confirm
   modelled-vs-measured labels remain visible, even at larger text.
8. Network offline/partial history/no data: clear error and empty-state
   instructions; no invented zeros, no hidden assumptions about health.

## VoiceOver manual checklist

On iPhone: **Settings → Accessibility → VoiceOver** (enable), or use
the Accessibility Shortcut configured by the user. On the Airwise page:

1. Swipe left/right through tab navigation: hear "Panoramica",
   "Inquinamento", "Diario e outdoor", plus selected state, without
   duplicate silent controls.
2. Visit the 7/14/30-day buttons: VoiceOver must announce the button
   label and pressed/selected state; changing the range should update
   the period date without moving focus unpredictably.
3. Use the headings rotor to find the main heading and analysis
   sections in a coherent order. There should be exactly one H1 per page.
4. Open daily/hourly and heatmap table alternatives: rotor navigation
   should announce row and column headers and units. Heatmap cells
   should expose missingness and sample counts, not only color.
5. Verify form labels, field values, number keyboard, error messages,
   and activity delete actions. Confirm alerts/status are announced.
6. Confirm that CAMS external estimates are announced as *modelled*
   values and never as Alexa observations.
7. Validate keyboard focus when an external keyboard is connected
   (Tab, Shift-Tab, Enter, Space): selected controls should remain
   visible above the fixed navigation.

## Automated evidence already available

- Synthetic Chrome Accessibility tree: named button/link controls,
  headings, navigation landmarks, interactive semantic table.
- Browser Tab traversal: 12 focus advances per page with visible
  focus styling.
- Reflow stress: 375 CSS-px mobile and 768/1280 CSS-px desktop with
  simulated 200% CSS zoom. This **does not** certify physical
  Safari 200% text resize or VoiceOver.
- Browser can test `aria-pressed` period state and populated accessible
  7×24 heatmap table, not VoiceOver pronunciation quality.

## Sign-off

- Chrome synthetic keyboard / Accessibility tree / 200% CSS zoom: **PASS**
- Real iPhone Safari portrait/landscape & text scaling: **NOT RUN**
- Physical iPhone VoiceOver: **NOT RUN**
- Manual iPhone form interaction and network source audit: **NOT RUN**

Do not merge or claim full WCAG / iOS accessibility certification
until the remaining required manual tests are run and evaluated.
