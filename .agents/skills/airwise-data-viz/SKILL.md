---
name: airwise-data-viz
description: Audit and improve Airwise PM2.5, VOC, CO, humidity, event, heatmap and indoor/outdoor graphs with honest units and accessible mobile chart interactions.
---

# Airwise Data Visualization and Evidence Integrity

## Source semantics before chart choices
- Alexa indoor: local hourly *aggregate measurements*. PM2.5 in µg/m³; VOC and Air Quality as indices; CO not a certified safety alarm; humidity %.
- Open-Meteo/CAMS outdoor: **gridded modelled estimate** with source and sync time, not the same instrument or an on-site measurement. Use amber and visually distinct stroke pattern.
- Diary events: human-reported observations. Use lilac markers, never label as identified causes.
- UTC canonical storage; local time zone for human circadian patterns. DST repeats can produce two local "02:00" samples. Missing observations are not zero.

## Choose an analytical view
| User question | View | Supporting evidence |
| --- | --- | --- |
| "What changed over the last days?" | UTC hourly trend, separate daily trend | sample count, range, coverage |
| "When does it happen?" | local-time hour profile + 7×24 heatmap | cell sample counts, DST label |
| "How severe/long were episodes?" | event duration + max + cumulative relative excess | statistical p90 is **not** health threshold |
| "Does outside track inside?" | two-series hourly overlay + paired-hour summary | source names, units, pair count, model disclaimer |
| "Could cooking be related?" | event marker + pre/post contrast | missingness and observational caveat |
| "Is a day above WHO 24h reference?" | complete-day mean with context | 15 µg/m³ environmental-air reference, not indoor-safety proof |

## Integrity gates
1. Every plotted value traceable to a timestamp, sensor, origin and unit.
2. Never interpolate gaps without user opt-in or silently merge :47 rolling hour averages with UTC-aligned samples.
3. A chart not navigable by touch/keyboard **must** have a nearby plain-data alternative; provide a simple table with the same period, sensors and values.
4. Limit mobile x-axis ticks: prefer 3–5 legible time landmarks and avoid rotated microtype. On dense charts use an explicit horizontally scrollable chart area with affordance, not page overflow.
5. Distinguish 0 from missing/null in both numbers and visual rendering.
6. Color is not the only channel: different line style, label and proximity, units near data.
7. Pairing for correlation uses exact UTC hours and sufficient n. Include r only when meaningful, never describe as causality; derived Alexa Air Quality index is not a separate cause.
8. Report unavailable, partial and stale states. The 29-day October backfill is historical, not live data.
9. Show reference guidelines as context only and with applicability caveats, no absolute health claims.

## Verification
- Test unit labels, gap discontinuities, true zeros, DST, relative p90 episodes, empty CAMS, stale outdoor model, 2-source comparison.
- Compare chart tooltip values with the raw API JSON for synthetic fixtures.
- On mobile, test tap/selection and data-table alternative at 320, 375 and 390px.
- Never publish private raw sensor series, coordinates or diary text as screenshot artifacts.

## Source ideas
Original synthesis of chart interaction and mobile legibility ideas from UI UX Pro Max, Vercel UX/a11y review patterns and Airwise's own sensor-science restrictions.
