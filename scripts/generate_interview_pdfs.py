"""Generate the numbered RFQ inputs used by the interview walkthrough."""

from pathlib import Path

from generate_fixtures import drawing
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/05-面试与问答/05-演示输入材料"
INK = HexColor("#19324b")
SECONDARY = HexColor("#4a6075")
PALE = HexColor("#eaf1f6")


def request_sheet(
    filename: str,
    title: str,
    reference: str,
    quantity: int,
    notes: tuple[str, ...],
) -> None:
    """Keep the machine-read fields as single, unambiguous text lines."""
    width, height = A4
    c = Canvas(str(OUTPUT / filename), pagesize=A4, invariant=1)
    c.setTitle(f"{title} | {reference}")
    c.setAuthor("RFQ Workbench - synthetic interview materials")
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(46, height - 57, "PRECISIONMOTION  /  PROCUREMENT")
    c.setFont("Helvetica-Bold", 27)
    c.drawString(46, height - 102, title)
    c.setFillColor(SECONDARY)
    c.setFont("Helvetica", 11)
    c.drawString(46, height - 127, reference)
    c.setFillColor(PALE)
    c.rect(46, height - 333, width - 92, 165, stroke=0, fill=1)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(62, height - 193, "REQUEST DETAILS")
    c.setFont("Helvetica", 13)
    facts = (
        "Part number: PM-BRKT-010",
        "Required revision: B",
        f"Quantity: {quantity}",
        "Currency: EUR",
    )
    for index, fact in enumerate(facts):
        c.drawString(62, height - 226 - 24 * index, fact)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(46, height - 383, "BUYER NOTES")
    c.setFont("Helvetica", 11)
    for index, note in enumerate(notes):
        c.drawString(46, height - 412 - 23 * index, note)
    c.setStrokeColor(PALE)
    c.line(46, 74, width - 46, 74)
    c.setFillColor(SECONDARY)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(46, 55, "SYNTHETIC INTERVIEW DATA  /  INTERNAL BUDGET DEMO")
    c.drawRightString(width - 46, 55, "1 / 1")
    c.save()


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    request_sheet(
        "01-RFQ-100件.pdf",
        "Request for quotation",
        "Buyer reference PM-INT-001",
        100,
        (
            "Prepare an internal budget for the actuator mounting plate.",
            "Use the released B drawing supplied with this request.",
            "Supplier availability, delivery and customer terms remain open.",
        ),
    )
    drawing(OUTPUT / "02-PM-BRKT-010-B版.pdf", "B", "Al-6061-T6", "mm")
    request_sheet(
        "03-客户数量澄清-500件.pdf",
        "Quantity clarification",
        "Buyer reference PM-INT-001 / clarification 01",
        500,
        (
            "Update the existing request from one hundred to five hundred pieces.",
            "The part, required drawing revision and currency are unchanged.",
            "Recalculate the internal budget for the revised lot.",
        ),
    )
    request_sheet(
        "04-RFQ-版次冲突.pdf",
        "Request for quotation",
        "Buyer reference PM-INT-003",
        100,
        (
            "Prepare an internal budget using released revision B.",
            "Check the revision on the drawing received with this request.",
            "If the drawing does not match, request the released drawing.",
        ),
    )
    drawing(OUTPUT / "05-PM-BRKT-010-A版.pdf", "A", "Al-6061-T6", "mm")
    drawing(OUTPUT / "06-PM-BRKT-010-材料缺失.pdf", "B", None, "mm")


if __name__ == "__main__":
    main()
