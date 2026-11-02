# Airwise Design System — v1.1 mobile-first

## Product and design intent

A **personal indoor air observatory**, not a marketing landing page and not a medical dashboard. The primary user wants to discover **what changed**, **when** it happened, and **what evidence supports a hypothesis** without implying causality.

Visual direction: **quiet scientific instrument**. Use a restrained, distinctive low-light atmosphere. Keep mint reserved for indoor measurements and active actions, amber for **external modelled estimates**, and lilac for manual diary annotations. Aesthetic impact comes from lucid data hierarchy, not decorative gradients or bloated hero text.

## Shared tokens

```css
:root {
  --aq-background: #0b121b;
  --aq-surface: #142534;
  --aq-surface-raised: #192f3c;
  --aq-border: #334b58;
  --aq-text: #f2faf7;
  --aq-muted: #b6c7d0;
  --aq-indoor: #9ce8c2;
  --aq-outdoor: #f0c58e;
  --aq-note: #d6b3f2;
  --aq-error: #ffb8bf;
  --aq-focus: #b6f6d4;
  --aq-radius-sm: 10px;
  --aq-radius-md: 16px;
  --aq-radius-lg: 22px;
  --aq-space-1: 4px;
  --aq-space-2: 8px;
  --aq-space-3: 12px;
  --aq-space-4: 16px;
  --aq-space-5: 20px;
  --aq-space-6: 24px;
  --aq-space-8: 32px;
  --aq-touch-target: 44px;
}
```

Typography: system sans, no remote fonts. Body **16px** on mobile (readable at default zoom); secondary annotations **at least 12px**, never hide essential units in tiny copy. Prefer tabular numerals and visible units. Use one typographic emphasis per content group.

## First-screen hierarchy

- **Overview**: compact identity, a real observable headline ("Qualità dell'aria"), most recent valid measurement + timestamp and data coverage; sensor navigation; chart.
- **Pollution**: PM2.5 episodes and evidence first; patterns next; associations last. Show model/measurement uncertainty near the figure.
- **Context**: compare indoor with optional outdoor, then diary actions. An empty outdoor state shows an understandable opt-in action and the privacy tradeoff.

Mobile navigation: three clear destinations, with persistent bottom navigation at narrow widths; retain meaningful document links for keyboard/desktop users. No unlabeled icon-only action.

## KPI density and visual weight — reviewed 2026-11-02

The previous UI and illustrative phone mock presented 4–5 KPI cards as equally
large, with numbers up to 39px on mobile. This violates the declared
**observatory / evidence-first** hierarchy: repeated big numbers push graphs and
interpretation below the fold. A responsive layout can be geometrically sound
and still be poorly prioritized.

**New visual budget at 320–390px:**
- One prominent **PM2.5 summary** on Overview (up to 28px numeral; ~85px
  card), always labelled as a period *average*, with source/coverage.
- Four secondary sensor metrics are **20px numerals** in compact two-column
  cells (~72px minimum card), not four simultaneous hero KPIs. Their units
  remain readable and no sensor value is color-coded as safe/unsafe.
- In Pollution, mediana, p90, episode count and coverage all use **~21px
  numerals** in ~84px two-column cards. The daily PM2.5 chart has higher
  visual priority. The p90 statistic is a relative percentile, not a hazard
  threshold.
- In Context, paired hours, indoor mean, and modelled outdoor mean are
  **~19px** compact supporting cells; the paired-line comparison is primary,
  with data provenance obvious.
- Mobile analytical headings generally **28px**, not giant marketing hero
  slogans. Reserve ~32px or more for a truly actionable primary state,
  not decorative statistics.

Use **information priority**, never font size alone, to emphasize content:
measurement + unit + interval + source + coverage are a *group*. Avoid
rendering a solitary bold numeral as a diagnosis or alarm. The chart and
its evidence should enter the viewport without excessive scrolling.

**Design QA gate:** check the vertical position of the first useful chart,
not only body overflow and hit-target size. In particular, compare
320/375/390px screenshot crops and whether the first 1–2 phone screens
answer "what happened and when?" Document if that condition fails.

## Composition

- Mobile 320–599px: one column, `padding-inline: 16px`, smaller title, native vertical scroll, no page-wide horizontal overflow. Only individual *optional* data rails or heatmaps may scroll sideways, with visible affordances.
- Tablet 600–1023px: content-first responsive grid with selective two-column cards.
- Desktop ≥1024px: two-column analytical workspaces, max-width about 1240px.
- Inputs min 16px to prevent mobile viewport zoom; actions min 44×44px with 8px between adjacent targets.
- Support safe-area insets, keyboard focus, reduced motion, tap and keyboard chart exploration. Never use hover as the only path to exact values.

## Chart grammar and evidence integrity

- PM2.5 indoors = mint solid line, `µg/m³`, Alexa aggregated measurement.
- Outdoor PM2.5 = amber dashed or differentiated line, CAMS modelled estimate, not a nearby instrument.
- Activities = lilac markers and descriptive notes, not causal attribution.
- Missing hours interrupt a line; never display missing as 0. UTC hour keys, local labels explicit, clock-change handling intentional.
- Charts have adjacent text summary + table or list alternatives for touch, keyboard and screen-reader users. Tapping a canvas is not the only way to get precise values.
- Time ranges, reference periods, sample sizes and coverage are legible without opening a tooltip.
- Preserve original data; no health alarms or comparisons between VOC index and concentrations.

## Anti-patterns

No generic giant marketing hero above actual data on mobile. No seven cards that look equal when only one is primary. No 10px core text, tiny 34px controls, desktop 24-column heatmap compressed to 320px, legend that depends solely on color, invisible overflow or decorative motion. Do not silently introduce cloud analytics or external font/CDN requests.

## Quality gates

Refer to `.agents/skills/airwise-visual-qa/SKILL.md`; all acceptance checks are release gates. Document deviations in `docs/design/audit-2026-11-02.md`.
