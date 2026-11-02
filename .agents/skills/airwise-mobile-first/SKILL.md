---
name: airwise-mobile-first
description: Redesign and implement Airwise HTML/CSS/JS mobile-first with thumb-friendly navigation, responsive layout, ergonomic forms and safe-area handling.
---

# Airwise Mobile-First Engineering

## Scope
Use for `dashboard.html`, `pollution.html`, `context.html` and any future local pages. This is a responsive web dashboard, **not** React Native, so translate only the relevant UX ideas.

## Required workflow
1. Inspect real page tasks and mark P0/P1/P2 findings with `file:selector:fix`. Read `DESIGN.md` and `docs/design/audit-2026-11-02.md`.
2. Build **mobile first** at width 320–390px, then 600/768/1024/1440px. The first 1–2 screens must answer "what is happening?" before complex charts. Preserve technical depth below fold.
3. Create a shared navigation model with three user-centric destinations (Overview, Pollution, Context). On narrow screens use a fixed bottom bar, large named targets, `env(safe-area-inset-bottom)`, and body padding to avoid obscuring data. On wide screens use a normal nav. No hidden fourth destination that requires hover.
4. All buttons, input controls, interactive cards: **≥44px hit area**, 8px spacing, visible focus. For input/select/textareas use **≥16px**, not placeholder-only labels. Keep actions specific and show loading/success/error locally.
5. Prevent **document-wide horizontal scroll** at 320px; use `min-width:0`, single-column cards and local `overflow-x:auto` only for an explicitly labelled chart, heatmap or optional rail. Chart axis and legend remain legible.
6. Treat canvas hover as enhancement. On tap show a selected value; pair charts with a text summary and a keyboard/screen-reader-accessible table or per-point list.
7. Respect `prefers-reduced-motion`; don't force scroll trapping, and don't disable zoom. Support 200% zoom and browser text scaling.
8. Test no data, stale data, partially available sensors, status errors, keyboard-only tab order, screen-reader labels, sunrise / daylight-saving time formatting.
9. Run `python3 scripts/check_mobile_ui.py`, unit tests and *actual* 320/375/390/768/1024/1440 browser screenshots where a renderer exists. Declare if screenshots were not run.

## Acceptance matrix
| Area | Pass |
| --- | --- |
| Navigation | All three pages in ≤1 tap from any page |
| Touch | ≥44px with adequate gaps |
| Forms | 16px text; readable errors; keyboard stays usable |
| Layout | Zero body overflow; charts contain any local scroll |
| Evidence | Units/time range/coverage always visible |
| A11y | Canvas has accessible alternative; focus visible; tap works |
| Safety | No external resources, opt-in CAMS only, Host/Origin boundary preserved |

## Never
Blindly install paid mobile design APIs, remote fonts, client-side tracking or map SDKs. Do not replace measured PM2.5 with a model estimate or suppress missing-data states. No fake progress bars or misleading health status.

## Source ideas
Original adaptation of touch/accessibility priorities from UI UX Pro Max, screen-to-screen coherence from Sleek, Vercel web interface checks and Impeccable adapt/harden. Details and links in `docs/design/skill-sources.md`.
