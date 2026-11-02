# Public GitHub skill research and attribution (2026-11-02)

This page records **design methodology research**. The Airwise SKILL.md documents are **original syntheses**, not vendor source copies. We do not bundle upstream code, design tables, proprietary scripts, images, licenses or external credentials.

| Source | GitHub snapshot: stars (2026-11-02) | Observed license | Valuable method | Airwise adaptation | Not adopted |
| --- | ---: | --- | --- | --- | --- |
| [Anthropic frontend-design](https://github.com/anthropics/skills/tree/main/skills/frontend-design) | ~186.5k repo | Apache-2.0 (skill LICENSE.txt) | Context-grounded art direction, anti-template critique | Observational/scientific UI identity, deliberate tokens | Their prose or implementation |
| [Vercel web-design-guidelines](https://github.com/vercel-labs/agent-skills/tree/main/skills/web-design-guidelines) | ~33.4k repo | No top-level LICENSE confirmed at inspection | Audit accessibility/UX with actionable file:line findings | Original pass/fail audit rubric and local tests | Live download requirement, verbatim external checklist |
| [UI UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) | ~144k repo | MIT | Prioritized rules and searchable domains (touch, responsive, charts) | Split by mobile UX, chart semantics and visual QA | Hundreds of styles/fonts and build-time external dependencies |
| [Impeccable](https://github.com/pbakaus/impeccable) | ~99.9k repo | Apache-2.0 | Distinct critique → adapt → harden → polish steps | Four separate tasks for a local data application | External CLI/binary; unlicensed copy of instructions |
| [Stitch Skills](https://github.com/google-labs-code/stitch-skills) | ~8.7k repo | Apache-2.0 | Source-of-truth design documentation + structural/visual separation | `DESIGN.md` and audit protocol | Stitch account, MCP or remote rendering |
| [Sleek mobile-app design](https://github.com/sleekdotdesign/agent-skills) | ~597 repo | MIT | Full-height mobile screenshot review and screen-to-code parity | Viewport QA rubric and tap-first verification | Sleek subscription/API/key, external screenshot upload |

**Popularity interpretation:** GitHub stars describe the repository, not a specific skill's accuracy, accessibility, or runtime suitability. A highly starred kit can still be wrong for a small local-first vanilla HTML+JS project.

## What was merged conceptually

1. *Direction* (Anthropic) + *tokens* (Stitch) + *critique mode* (Impeccable) → `airwise-design-direction`.
2. *Mobile priority* (UI UX Pro Max) + *native interactions* (Sleek, as method) + *forms/a11y audit* (Vercel) → `airwise-mobile-first`.
3. *Charts and semantics* (UI UX Pro Max and Vercel) + Airwise measurements/modelled distinction → `airwise-data-viz`.
4. *Audit/polish loop* (Impeccable) + *file/line findings* (Vercel) + *screenshot parity* (Sleek) → `airwise-visual-qa`.

### Licensing policy

Every document in `.agents/skills/airwise-*/` is newly authored for this repo. No third-party code or bulk text is copied. Links above attribute conceptual influences. Any future verbatim reuse must verify upstream license and retain its legally required notices; Vercel's missing license is a particular reason to borrow only general ideas.
