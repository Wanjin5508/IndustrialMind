# Evaluation boundary

Owner: Engineering · Status: M0 baseline · Updated: 2026-11-08

- `gold/dev.json` is assistant-authored development truth, independently checked against calculations and rendered PDFs. It is not customer-validated manufacturing data.
- `fixtures/dev` contains six development packages, including controlled negative cases.
- M7 will add **12 independently authored frozen packages** (4 normal, 3 revision conflicts, 3 missing fields, 2 unit/rate exceptions). These do not yet exist and are not counted as passed evaluations.
- A part and all its revisions/degraded variants remain in one split. Frozen cases must use separate parts/layouts within the supported family.
- Public drawings, if licensed, belong to a separate external set. No external dataset is bundled at M0.
- Runtime code may access `fixtures/dev` and `fixtures/reference`, never `evals/gold` or `fixtures/authoring`.
- Report numerator, denominator, abstentions, failures, configuration and measured timing. Synthetic tests do not establish customer accuracy, time savings or manufacturing feasibility.
