---
name: airwise-design-direction
description: Define or review Airwise's distinctive scientific UI direction, information hierarchy and semantic design tokens before implementing any screen.
---

# Airwise Design Direction

## When to invoke
New screen, visual rebrand, layout redesign, inconsistent component styles, or a review that says Airwise feels too generic.

## Inputs
Read `DESIGN.md`, the relevant HTML, the task the user actually wants to do, and one representative data scenario (valid, missing and stale samples). Do not choose fonts/palettes before defining the task.

## Procedure
1. **State the job-to-be-done in one sentence**: e.g. "Check if PM2.5 is unusually elevated and what was happening." Identify the primary datum and its uncertainty.
2. **Inventory existing tokens and brand semantics.** Color signifies *source*: indoor mint, outdoor model amber, activity note lilac; never imply good/bad from mint or red without an explicit, valid rule.
3. **Draw mobile-first ASCII wireframes** for a 375px screen and desktop 1280px. Place one user decision above the fold; less important panels further down. Try removing the marketing headline before removing measurement context.
4. **Choose one visual signature**: quiet observatory instrumentation, precision, breathable white space, honest data glyphs. Do not make all cards the same weight. Avoid unnecessary gradients or eye-catching flourishes.
5. **Refine a 4–6 role palette** and a compact type/spacing/radius scale in `DESIGN.md`; keep remote dependencies out.
6. **Critique and simplify**: ask whether the screen could belong to a finance dashboard simply by changing labels. If so, make its air-quality-specific story and uncertainty more visible.
7. **Hand off** exact components/roles and contrast notes to the mobile-first skill; afterward do visual QA.

## Output contract
Produce (or update): a short `DESIGN.md` section; mobile and desktop hierarchy; tokens; 3 things retained; 3 things eliminated; intended quality gates. Changes must be reviewable against source, not just adjectives.

## Product constraints
No ambient monitoring health diagnoses. Distinguish measured vs modelled sources. Keep all readings and diary notes local. Never invent sample values for UI screenshots without explicitly marking them synthetic.

## Source ideas
Conceptual inspiration: Anthropic frontend-design (intentional art direction), Google Stitch design-md (semantic source of truth), Impeccable critique/polish process. Original skill written for Airwise; see `docs/design/skill-sources.md`.
