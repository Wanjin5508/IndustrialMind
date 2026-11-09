"""M3 guards against valid-but-irrelevant citations and manufactured facts."""

from dataclasses import replace
from datetime import date

import pytest
from conftest import ENGINEER
from pydantic import ValidationError
from test_files import analyze, cost, fixture_run

from rfq_workbench.contracts import ExtractedValue, Source
from rfq_workbench.extraction import LocalTextProvider
from rfq_workbench.model_usage import estimate_cost


@pytest.mark.parametrize("raw", ["", "  ", "UNKNOWN", "[not provided]", "null", "未提供"])
def test_missing_value_normalization(raw):
    assert ExtractedValue(value=raw, source_id=None).value is None


@pytest.mark.parametrize("raw", [123, True, {}, "x" * 201])
def test_reject_invalid_model_field(raw):
    with pytest.raises(ValidationError):
        ExtractedValue(value=raw, source_id=None)


@pytest.mark.parametrize(
    "variant,expected",
    [
        ("wrong_document", "INVALID_SOURCE"),
        ("unrelated_line", "TEXT_MISMATCH"),
        ("stale_id", "INVALID_SOURCE"),
        ("wrong_value", "TEXT_MISMATCH"),
    ],
)
def test_unverified_citations_block(client, variant, expected):
    class Provider:
        def extract(self, body):
            result = LocalTextProvider().extract(body)
            f = result.fields.drawing.material
            if variant == "wrong_document":
                f.source_id = result.fields.rfq.part_number.source_id
            elif variant == "unrelated_line":
                f.source_id = result.fields.drawing.revision.source_id
            elif variant == "stale_id":
                f.source_id = "previous-task:p1:l4"
            else:
                f.value = "Steel-S235"
            return result

    client.app.state.file_workflow.providers["local_text"] = Provider()
    run = analyze(client, fixture_run(client))
    assert run["fields"]["drawing.material"]["evidence"]["status"] == expected
    assert cost(client, run).status_code == 422


def test_numeric_formatting_is_not_a_false_mismatch(client):
    class Provider:
        def extract(self, body):
            result = LocalTextProvider().extract(body)
            result.fields.drawing.width_mm.value = "80"
            return result

    client.app.state.file_workflow.providers["local_text"] = Provider()
    run = analyze(client, fixture_run(client))
    assert run["fields"]["drawing.width_mm"]["evidence"]["status"] == "TEXT_VERIFIED"
    assert cost(client, run).status_code == 200


def test_document_internal_conflict_blocks_model_selection(client):
    from conftest import ROOT

    drawing = (ROOT / "fixtures/dev/drawings/PM-BRKT-010_B.pdf").read_bytes()
    text = (ROOT / "fixtures/dev/DEV-001/rfq.md").read_bytes() + b"\nQuantity: 500\n"
    run = client.post(
        "/runs/upload",
        headers=ENGINEER,
        files={"rfq": ("rfq.md", text), "drawing": ("drawing.pdf", drawing)},
    ).json()

    class Provider:
        def extract(self, body):
            result = LocalTextProvider().extract(body)
            citation = next(s for s in body["sources"] if s["text"] == "Quantity: 100")
            result.fields.rfq.quantity = ExtractedValue(value="100", source_id=citation["id"])
            return result

    client.app.state.file_workflow.providers["local_text"] = Provider()
    run = analyze(client, run)
    assert run["fields"]["rfq.quantity"]["evidence"]["status"] == "AMBIGUOUS"
    assert cost(client, run).status_code == 422


def test_visual_page_requires_manual_confirmation(client):
    # Image-only PDF is built from the real rendered fixture page, with no text layer.
    from io import BytesIO

    from conftest import ROOT
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen.canvas import Canvas

    normal = fixture_run(client)
    doc = next(d for d in normal["documents"] if d["kind"] == "drawing")
    image = client.get(
        f"/runs/{normal['id']}/documents/{doc['id']}/pages/1", headers=ENGINEER
    ).content
    pdf = BytesIO()
    canvas = Canvas(pdf)
    canvas.drawImage(ImageReader(BytesIO(image)), 0, 0, width=595, height=842)
    canvas.save()
    scan = client.post(
        "/runs/upload",
        headers=ENGINEER,
        files={
            "rfq": ("rfq.md", (ROOT / "fixtures/dev/DEV-001/rfq.md").read_bytes()),
            "drawing": ("scan.pdf", pdf.getvalue()),
        },
    ).json()

    class Provider:
        def extract(self, body):
            result = LocalTextProvider().extract(normal)
            local_rfq = LocalTextProvider().extract(body).fields.rfq
            result.fields.rfq = local_rfq
            page = next(
                s for s in body["sources"] if s["document_id"] == body["documents"][1]["id"]
            )
            for key in type(result.fields.drawing).model_fields:
                getattr(result.fields.drawing, key).source_id = page["id"]
            return result

    client.app.state.file_workflow.providers["local_text"] = Provider()
    run = analyze(client, scan)
    fact = run["fields"]["drawing.material"]
    assert fact["evidence"]["status"] == "VISUAL_REVIEW"
    assert fact["evidence"]["locator"] == "page" and not fact["evidence"]["value_supported"]
    assert cost(client, run).status_code == 422
    response = client.patch(
        f"/runs/{run['id']}/fields",
        headers=ENGINEER,
        json={
            "expected_version": run["version"],
            "values": {k: f["value"] for k, f in run["fields"].items() if k.startswith("drawing.")},
            "reason": "Synthetic scan reviewed against original drawing in test",
        },
    )
    reviewed = response.json()
    assert reviewed["fields"]["drawing.material"]["human_confirmed"] is True
    assert reviewed["fields"]["drawing.material"]["evidence"]["status"] == "VISUAL_REVIEW"
    assert cost(client, reviewed).status_code == 200


def test_bbox_contract_rejects_invented_coordinates():
    with pytest.raises(ValidationError):
        Source(id="x", document_id="d", page=1, text="", bbox=[0, 0, 2, 1])


def test_cost_estimate_and_unknowns(settings):
    settings = replace(settings, model_base_url="https://api.deepseek.com")
    usage = {
        "input_tokens": 1000,
        "output_tokens": 500,
        "input_tokens_details": {"cached_tokens": 200},
    }
    result = estimate_cost(settings, "deepseek-flash", usage, date(2026, 11, 9))
    assert result["estimated_cost"] == "0.00084120"
    assert result["cost_currency"] == "USD" and result["pricing"]["sha256"]
    assert estimate_cost(settings, "other", usage)["estimated_cost"] is None
    assert estimate_cost(settings, "deepseek-flash", None)["estimated_cost"] is None
    assert (
        estimate_cost(settings, "deepseek-flash", usage, date(2027, 1, 1))["estimated_cost"] is None
    )
    assert (
        estimate_cost(
            replace(settings, model_base_url="https://example.com"), "deepseek-flash", usage
        )["estimated_cost"]
        is None
    )


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 301])
def test_invalid_timeout_fails_at_startup(settings, timeout):
    with pytest.raises(ValueError):
        replace(settings, model_timeout=timeout)
