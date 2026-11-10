"""Read-only adapter contracts and synthetic implementations for M6."""

from decimal import Decimal
from typing import Literal

from rfq_workbench.contracts import Contract, Quote
from rfq_workbench.costing import fingerprint
from rfq_workbench.errors import DomainError
from rfq_workbench.service import Workflow, require_intent


class PLMRevision(Contract):
    released_revision: str
    rfq_revision: str | None
    drawing_revision: str | None
    reference_id: str


class ERPDraft(Contract):
    preview_id: str
    draft_version: int
    approval_status: Literal["PENDING", "APPROVED"]
    quantity: int
    currency: str
    unit_cost: Decimal
    unit_price: Decimal
    total_price: Decimal
    input_hash: str
    reference_hash: str


class MESFeedback(Contract):
    sample_id: str
    revision: str
    operation: str
    planned_minutes_per_part: Decimal
    observed_minutes_per_part: Decimal


class IntegrationPreview(Contract):
    mode: Literal["SIMULATED"] = "SIMULATED"
    run_version: int
    part_number: str
    plant: str
    plm: PLMRevision
    erp: ERPDraft | None = None
    erp_status: Literal["NO_DRAFT", "STALE_DRAFT", "AVAILABLE"]
    mes: MESFeedback


class SimulatedPLM:
    def read_revision(self, rfq_revision: str | None, drawing_revision: str | None) -> PLMRevision:
        return PLMRevision(
            released_revision="B",
            rfq_revision=rfq_revision,
            drawing_revision=drawing_revision,
            reference_id="SIM-PLM-PM-BRKT-010-B",
        )


class SimulatedERP:
    def preview(self, run_id: str, quote: Quote, approved: bool) -> ERPDraft:
        return ERPDraft(
            preview_id=f"sim-erp-{run_id}-{quote.draft_version}",
            draft_version=quote.draft_version,
            approval_status="APPROVED" if approved else "PENDING",
            quantity=quote.quantity,
            currency=quote.currency,
            unit_cost=quote.unit_cost,
            unit_price=quote.unit_price,
            total_price=quote.total_price,
            input_hash=quote.input_hash,
            reference_hash=quote.reference_hash,
        )


class SimulatedMES:
    def read_feedback(self) -> MESFeedback:
        return MESFeedback(
            sample_id="SIM-MES-PM-BRKT-010-B-001",
            revision="B",
            operation="铣削与钻孔",
            planned_minutes_per_part=Decimal("7.2"),
            observed_minutes_per_part=Decimal("8.1"),
        )


def preview_integrations(body: dict, actor: str, workflow: Workflow) -> IntegrationPreview:
    require_intent(body, ("prepare_quote", "review_drawing"))
    inputs = body["inputs"] or {}

    def fact(key: str) -> str | None:
        field = body["fields"].get(key, {})
        if (
            field.get("human_confirmed")
            or field.get("evidence", {}).get("status") == "TEXT_VERIFIED"
        ):
            return field["value"]
        return None

    part = inputs.get("part_number") or fact("rfq.part_number") or fact("drawing.part_number")
    plant = inputs.get("plant", "DE-DEMO")
    if (
        body["project_id"] != "PM-DEMO"
        or plant != "DE-DEMO"
        or part != "PM-BRKT-010"
        or inputs.get("product_family", "mounting-bracket") != "mounting-bracket"
    ):
        raise DomainError("INTEGRATION_UNAVAILABLE", "当前零件没有可用的模拟系统记录。", 404)
    from rfq_workbench.integrations.snapshots import COST_FIELDS

    plm_raw = workflow.systems.plm.read_item_revision(part, plant)
    workflow.snapshots.save(body["id"], plm_raw, ("part_number", "plant", "revisions"))
    erp_raw = workflow.systems.erp.read_cost_reference(
        inputs.get("material") or fact("drawing.material") or "", plant
    )
    workflow.snapshots.save(body["id"], erp_raw, COST_FIELDS)
    mes_raw = workflow.systems.mes.read_feedback(part, plant)
    workflow.snapshots.save(
        body["id"],
        mes_raw,
        (
            "part_number",
            "plant",
            "sample_id",
            "revision",
            "operation",
            "planned_minutes_per_part",
            "observed_minutes_per_part",
        ),
    )
    from datetime import date

    released = [
        row["revision"]
        for row in plm_raw.payload["revisions"]
        if row["status"] == "RELEASED"
        and date.fromisoformat(row["valid_from"])
        <= workflow.clock.now().date()
        <= date.fromisoformat(row["valid_until"])
    ]
    if len(released) != 1:
        raise DomainError("SOURCE_CONFLICT", "PLM 当前发布版次无法唯一确定。")
    result = IntegrationPreview(
        run_version=body["version"],
        part_number=part,
        plant=plant,
        plm=PLMRevision(
            released_revision=released[0],
            rfq_revision=inputs.get("required_revision") or fact("rfq.required_revision"),
            drawing_revision=inputs.get("drawing_revision") or fact("drawing.revision"),
            reference_id=plm_raw.ref.external_id,
        ),
        erp_status="NO_DRAFT",
        mes=MESFeedback(**{key: mes_raw.payload[key] for key in MESFeedback.model_fields}),
    )
    if body["quote"] is not None:
        if not workflow.references_match(body, actor):
            result.erp_status = "STALE_DRAFT"
        else:
            approval = body["approval"] or {}
            result.erp_status = "AVAILABLE"
            result.erp = SimulatedERP().preview(
                body["id"],
                Quote.model_validate(body["quote"]),
                approval.get("quote_hash") == fingerprint(body["quote"]),
            )
    return result
