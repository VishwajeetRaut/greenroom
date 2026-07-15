# Design Document Version History

Every unique version of the project's design document, extracted from git history (oldest first). Files are named `<date>_<commit>_<subject-slug>.md`.

## Images

`images/` holds every unique version of the diagrams these docs embed
(deduped by content, same as the docs themselves), named
`<date>_<commit>_<original-filename>`. Each snapshot above links to the
image versions that were actually live as of that commit, so opening an
older snapshot shows the diagrams as they looked at the time, not today's:

| File | Versions |
|---|---|
| `architecture.svg` | 1 — added 2026-06-24 ([2c01196](https://github.com/VishwajeetRaut/greenroom/commit/2c01196)), used only by the two v2.0-era snapshots before the doc moved to `docs/diagrams/*.png` |
| `architecture.png` | 2 — 2026-06-30 ([33152fa](https://github.com/VishwajeetRaut/greenroom/commit/33152fa)), 2026-07-08 ([9b380dd](https://github.com/VishwajeetRaut/greenroom/commit/9b380dd), current) |
| `user-flow.png` | 2 — same two commits |
| `developer-flow.png` | 2 — same two commits |
| `legend.png` | 1 — added 2026-06-30, never changed since; not embedded inline in any snapshot's Markdown but kept for completeness |

The 2026-06-17 (POC) and 2026-07-08 `e52a88f` snapshots have no diagrams —
neither version embedded any images.

| Date | Commit | Message |
|---|---|---|
| 2026-06-17 | [e878d99](https://github.com/VishwajeetRaut/greenroom/commit/e878d99) | [feat: LangChain LCEL agent, bug fixes, POC design doc](2026-06-17_e878d99_feat-langchain-lcel-agent-bug-fixes-poc-design-doc.md) |
| 2026-06-24 | [2c01196](https://github.com/VishwajeetRaut/greenroom/commit/2c01196) | [docs: update design doc v2.0 + architecture diagram](2026-06-24_2c01196_docs-update-design-doc-v2-0-architecture-diagram.md) |
| 2026-06-24 | [8a121d6](https://github.com/VishwajeetRaut/greenroom/commit/8a121d6) | [Update in design document](2026-06-24_8a121d6_update-in-design-document.md) |
| 2026-06-30 | [33152fa](https://github.com/VishwajeetRaut/greenroom/commit/33152fa) | [docs: add industry-grade design document with architecture diagrams and full audit](2026-06-30_33152fa_docs-add-industry-grade-design-document-with-architecture-di.md) |
| 2026-07-01 | [f3dd313](https://github.com/VishwajeetRaut/greenroom/commit/f3dd313) | [docs: fix consistency issues and remove redundant sections from DESIGN.md](2026-07-01_f3dd313_docs-fix-consistency-issues-and-remove-redundant-sections-fr.md) |
| 2026-07-01 | [8511c88](https://github.com/VishwajeetRaut/greenroom/commit/8511c88) | [Remove a redundant section from the design doc](2026-07-01_8511c88_remove-a-redundant-section-from-the-design-doc.md) |
| 2026-07-08 | [e52a88f](https://github.com/VishwajeetRaut/greenroom/commit/e52a88f) | [docs: update DESIGN.md to v4.0 — question bank expansion, new services, concurrency/scalability/rate-limit feedback, PlantUML diagrams](2026-07-08_e52a88f_docs-update-design-md-to-v4-0-question-bank-expansion-new-se.md) |
| 2026-07-08 | [7b46b81](https://github.com/VishwajeetRaut/greenroom/commit/7b46b81) | [docs: update DESIGN.md to v4.0 with new diagrams and industry-grade structure](2026-07-08_7b46b81_docs-update-design-md-to-v4-0-with-new-diagrams-and-industry.md) |
| 2026-07-08 | [92b5fa3](https://github.com/VishwajeetRaut/greenroom/commit/92b5fa3) | [docs: apply fork edits, remove em dashes, fix broken section references](2026-07-08_92b5fa3_docs-apply-fork-edits-remove-em-dashes-fix-broken-section-re.md) |
| 2026-07-15 | [a1e1533](https://github.com/VishwajeetRaut/greenroom/commit/a1e1533) **(current)** | [docs: update DESIGN.md with everything merged since the last revision](2026-07-15_a1e1533_docs-update-design-md-with-everything-merged-since-the-la.md) |
