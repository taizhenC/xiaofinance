# infinance / xiaofinance — Product Roadmap (v1.0 → Public Release)

Prepared: 2026-07-12 · Basis: full review of the codebase at commit `6285a85` (branch `fix/login-timeout`)

This roadmap evolves the current MVP — a local, single-user Xiaohongshu (XHS) US-stock
sentiment dashboard — into its next major stage: **a viable public release**.

## Documents

| File | Contents |
|---|---|
| [executive_summary.md](executive_summary.md) | What the product is, the strategic call for the next stage, headline priorities, release gate, success metrics |
| [product_analysis.md](product_analysis.md) | Deduced core features, unique value proposition, mission, direction, target audience, honest current-state assessment |
| [feature_roadmap.md](feature_roadmap.md) | The full backlog — every item tagged `[Feature]` `[Bug Fix]` `[Refactor]` `[UI/UX]` and ranked P0 / P1 / P2, with rationale and done-when criteria |
| [technical_architecture.md](technical_architecture.md) | Current architecture assessment, proposed refactors with justification, target architecture, scaling and risk notes |

## Non-negotiable product principle

> **We summarize information; the user decides.** The product never recommends a stock,
> never generates buy/sell signals, and never ranks by "attractiveness" — only by
> conversation volume and observed data. The hit-rate scoreboard evaluates *the crowd's*
> track record, not the user's choices. Every roadmap item below was screened against
> this principle.

## Priority semantics

- **P0 (Critical)** — must ship before the public release. The release is gated on all P0 items.
- **P1 (High)** — fast-follows in the first 1–2 post-release cycles; high value, not release-blocking.
- **P2 (Low)** — future considerations; revisit after P1 outcomes are measured.
