# Evaluation boundary

Owner: Engineering · Status: M0 baseline · Updated: 2026-11-08

- `gold/dev.json` is assistant-authored development truth, independently checked against calculations and rendered PDFs. It is not customer-validated manufacturing data.
- `fixtures/dev` contains six development packages, including controlled negative cases.
- M7 will add **12 independently authored frozen packages** (4 normal, 3 revision conflicts, 3 missing fields, 2 unit/rate exceptions). These do not yet exist and are not counted as passed evaluations.
- A part and all its revisions/degraded variants remain in one split. Frozen cases use separate parts and layouts. A quantity change does not create an independent drawing sample.
- Public drawings, if licensed, belong to a separate external set. No external dataset is bundled at M0.
- Runtime code may access `fixtures/dev` and `fixtures/reference`, never `evals/gold` or `fixtures/authoring`.
- Report numerator, denominator, abstentions, failures, configuration and measured timing. Synthetic tests do not establish customer accuracy, time savings or manufacturing feasibility.

## M7 scoring tracks

| Track | Eligibility and scoring |
|---|---|
| Extraction and evidence | Use independent parts and layouts. Score extracted fields, supported evidence and abstentions against document truth. A correct refusal to quote an unsupported part is scored separately from extraction. |
| End-to-end quotation | A positive case needs independently frozen approved manufacturing and knowledge references and independently calculated expected amounts and material quantities. Use an isolated per-case configuration and database for eligible evaluation parts. Score applicability, blocking, amounts, approval, download and invalidation against the expected business outcome. |

Before scoring, pin the document bytes, approved references, gold labels, split and evaluation configuration. Use development data for fixes. Keep gold separate from model input and knowledge retrieval. The interactive demo remains single-part; an isolated evaluation reference does not extend its supported scope. The two tracks share no aggregate accuracy denominator. Fault cases and external public samples have separate reporting. Frozen inputs and scored results are pending M7.
