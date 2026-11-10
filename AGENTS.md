# Airwise agent operating instructions

This repository contains a local-only Amazon air-quality monitor and a responsive browser interface.

## Design work: mandatory skill router

Before any UI change, read these local original Agent Skills (open Agent Skills SKILL.md format):

1. `.agents/skills/airwise-design-direction/SKILL.md` — purpose, distinctive visual direction, token system and design review
2. `.agents/skills/airwise-mobile-first/SKILL.md` — information architecture, safe areas, touch ergonomics and responsive implementation
3. `.agents/skills/airwise-data-viz/SKILL.md` — measured vs modelled PM2.5, units, chart integrity and accessible data
4. `.agents/skills/airwise-visual-qa/SKILL.md` — final viewport, keyboard and data-state gates

When the task is an **audit**, start with visual-qa, and use the others for repairs.
When building a screen, use design-direction → mobile-first → data-viz (if charts) → visual-qa.
Read `DESIGN.md` and `docs/design/skill-sources.md` for shared tokens, rationale and provenance.

Preserve: localhost-only network boundary; private journals; opt-in external CAMS calls; no browser/CDN dependencies; hourly UTC vs displayed Europe/Rome time; PM2.5 vs modelled outdoor; no health/safety claims from consumer monitor; no new data-collection automations without permission.

Never upload screenshots with readings, account tokens, precise coordinates or diary notes to public Actions artifacts. For rendered QA, use synthetic fixtures or private Mac-local screenshots.

Before commit, execute `python3 -m unittest discover -s tests -v` and
`python3 scripts/check_mobile_ui.py`; inspect 320/375/390/768/1024/1440 px where an actual renderer is available. Report any viewport that could not be visually verified. Do not treat a green syntax test as a screenshot review.
