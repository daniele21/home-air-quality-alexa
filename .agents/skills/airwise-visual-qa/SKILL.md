---
name: airwise-visual-qa
description: Review and validate Airwise visual design, accessibility, responsive layout and edge states through severity-ranked audit findings and explicit test evidence.
---

# Airwise Visual QA / Critique / Polish

## Invocation
Use before a UI PR merge, after a mobile-first implementation, or for a visual bug report. This is a **quality gate**, not permission to invent screenshots.

## Four phases
1. **Audit:** examine all three pages and their shared navigation; log P0 blockers, P1 interaction/accessibility, P2 polish. State concrete selectors or file:line and user impact.
2. **Critique:** rank by task success, data credibility, accessibility then distinctiveness. Prefer eliminating unnecessary chrome over adding ornaments.
3. **Harden:** test no data, partial data, stale measurement, HTML escaping of diary notes, invalid outdoor location, HTTP 400, no network, iOS keyboard viewport.
4. **Polish & verify:** compare real browser screenshots side-by-side at 320, 375, 390, 768, 1024, 1440 px, zoom 200%, landscape and reduced-motion. Do not claim render verification when only a parser has run.

## Mechanical checks
Run `python3 scripts/check_mobile_ui.py` and `python3 -m unittest discover -s tests -v`. Treat these as *static and unit checks only*.
If a browser runner is available, measure:
- `document.documentElement.scrollWidth <= window.innerWidth`
- All tap targets bounding boxes >=44×44 px except inline text links that have appropriate target separation
- No chart legend/tooltip hidden behind fixed bottom nav or keyboard
- No line/color ambiguous to a color-vision-deficient reader
- Keyboard tab reaches every action; focus visible; Escape dismisses overlays; no focus traps
- Canvas exact values reachable by tap and accessible alternative
- Data source, units, last-updated time, sample count, missingness visually inspectable

## Deliverable
One table with `priority | file:selector | issue | evidence | fix | retest`. Include before/after screenshots **only if captured using synthetic fixtures**. End with `PASS`, `PARTIAL (missing live visual QA)` or `FAIL` with missing gates. Don't make a green test imply perfect UX.

## Source ideas
Original Airwise implementation of Impeccable's audit/critique/adapt/harden/polish sequence, Vercel actionable findings, and Sleek's full-height screen comparison. See source research.
