"""Resolve model citations only against this task's parser-issued sources.

A real source ID is not proof of a fact. Text verification requires the named
label and a matching value; only a whole PDF page can request visual review.
"""

import re
from decimal import Decimal

from rfq_workbench.contracts import EvidenceCheck, ExtractedValue, Issue, ReviewedField, Source

NUMERIC_FIELDS = {"quantity", "width_mm", "height_mm", "thickness_mm", "hole_diameter_mm"}
REVISION_FIELDS = {"rfq.required_revision", "drawing.revision"}


def revision_issue(key: str, field: dict) -> Issue | None:
    """A confirmed reading may stand, but the document's revision cannot be overwritten."""
    status = field.get("evidence", {}).get("status")
    if (
        field.get("value")
        and field["value"] == field.get("original_value")
        and field.get("source_valid")
        and (
            status == "TEXT_VERIFIED" or status == "VISUAL_REVIEW" and field.get("human_confirmed")
        )
    ):
        return None
    return Issue(
        code="REVISION_EVIDENCE_REQUIRED",
        field=key,
        message="请核对原文件版次。版次不同或无法确认时，请补充有效文件。",
    )


def equivalent(key: str, left: str, right: str) -> bool:
    if key in NUMERIC_FIELDS and all(
        re.fullmatch(r"\d+(?:\.\d+)?", v.strip()) for v in (left, right)
    ):
        return Decimal(left) == Decimal(right)
    return left.strip().casefold() == right.strip().casefold()


def verify(body: dict, kind: str, key: str, label: str, extracted: ExtractedValue) -> dict:
    source = next((s for s in body["sources"] if s["id"] == extracted.source_id), None)
    document = next(
        (d for d in body["documents"] if source and d["id"] == source["document_id"]), None
    )
    valid = bool(
        source
        and document
        and document["kind"] == kind
        and 1 <= source["page"] <= len(document["pages"])
    )
    if valid:
        Source.model_validate(source)
    locator = source["locator"] if valid else None
    value = extracted.value
    status, message = (
        "INVALID_SOURCE",
        "Citation does not resolve to the required document in this task.",
    )
    pattern = re.compile(r"^" + re.escape(label) + r":\s*(.+?)\s*$", re.I | re.M)
    if value is None:
        status, message = "MISSING", "No usable value; do not infer a replacement."
    elif valid:
        all_values = [m.group(1) for p in document["pages"] for m in pattern.finditer(p["text"])]
        ambiguous = (
            any(not equivalent(key, v, all_values[0]) for v in all_values[1:])
            if all_values
            else False
        )
        cited_values = [m.group(1) for m in pattern.finditer(source["text"])]
        if ambiguous:
            status, message = (
                "AMBIGUOUS",
                "Conflicting values for the same field occur in this document.",
            )
        elif any(equivalent(key, value, v) for v in cited_values):
            status, message = "TEXT_VERIFIED", "Named field and value match the cited text."
        elif (
            document["suffix"] == ".pdf"
            and source["id"] == f"{document['id']}:p{source['page']}"
            and not all_values
        ):
            status, message = (
                "VISUAL_REVIEW",
                "PDF page exists; visual value requires documented human confirmation.",
            )
        else:
            status, message = "TEXT_MISMATCH", "Cited text does not support this field/value."
    evidence = EvidenceCheck(
        status=status,
        source_exists=valid,
        value_supported=status == "TEXT_VERIFIED",
        locator=locator,
        message=message,
    )
    return ReviewedField(
        **extracted.model_dump(),
        original_value=value,
        source_valid=valid,
        needs_visual_review=status == "VISUAL_REVIEW",
        human_confirmed=False,
        evidence=evidence,
    ).model_dump()
