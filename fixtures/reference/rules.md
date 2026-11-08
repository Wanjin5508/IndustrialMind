# Demo rule register

SYNTHETIC DEMO DATA. Owner: RFQ engineering. Version: rules-demo-1.

| Rule | Condition | Result |
|---|---|---|
| REVISION_CONFLICT | Requested revision differs from current drawing | Engineering review; no approval |
| PART_CONFLICT | RFQ and drawing part identifiers differ | Engineering review |
| MISSING_FIELD | Required value absent or unsupported by source | Keep null; engineering clarification |
| UNSUPPORTED_MATERIAL | Material differs from the approved demo template | No automatic costing |
| UNSUPPORTED_UNIT | Drawing unit is not mm | No implicit conversion |
| INVALID_RATE | Rate data is expired, unapproved, or invalid | No calculation or export |
| STALE_APPROVAL | Input, document, rule, template, or rate changed | Invalidate quote approval |
| INTERNAL_ONLY | Procurement, capacity, delivery, or commercial terms pending | Internal budget draft only |

The application must enforce authorization and optimistic concurrency independently of model output.
