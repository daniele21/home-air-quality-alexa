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
