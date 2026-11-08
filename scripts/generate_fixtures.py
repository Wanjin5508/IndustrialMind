"""Create original, deterministic development PDFs and source manifests.

This script never writes evaluation gold. Expected outputs are maintained
separately so changing the generator cannot silently change the acceptance oracle.
"""

import hashlib
import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
DRAWINGS = ROOT / "fixtures/dev/drawings"
CASES = [
    ("DEV-001", "Normal / 100 parts", 100, "B", "Al-6061-T6", "mm"),
    ("DEV-002", "Quantity / 500 parts", 500, "B", "Al-6061-T6", "mm"),
    ("DEV-003", "Revision conflict", 100, "A", "Al-6061-T6", "mm"),
    ("DEV-004", "Missing material", 100, "B", None, "mm"),
    ("DEV-005", "Unsupported unit", 100, "B", "Al-6061-T6", "inch"),
    ("DEV-006", "No approved material template", 100, "B", "Steel-S235", "mm"),
]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )


def drawing(path: Path, revision: str, material: str | None, unit: str) -> None:
    c = canvas.Canvas(str(path), pagesize=landscape(A4), invariant=1)
    width, height = landscape(A4)
    c.setTitle(f"PM-BRKT-010 revision {revision} - SYNTHETIC")
    c.setAuthor("RFQ Workbench / original synthetic fixture")
    c.setStrokeColor(colors.HexColor("#23364b"))
    c.rect(24, 24, width - 48, height - 48)
    c.setFont("Helvetica-Bold", 17)
    c.drawString(42, height - 55, "PrecisionMotion | Mounting plate")
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#a65814"))
    c.drawString(42, height - 75, "SYNTHETIC DEMO DATA - NOT FOR MANUFACTURING")
    c.setFillColor(colors.HexColor("#23364b"))
    x, y, scale = 110, 245, 4
    c.setLineWidth(1.2)
    c.rect(x, y, 80 * scale, 50 * scale)
    for px, py in [(10, 10), (70, 10), (10, 40), (70, 40)]:
        c.circle(x + px * scale, y + py * scale, 6.5 * scale / 2)
        c.setLineWidth(0.4)
        c.line(x + px * scale - 18, y + py * scale, x + px * scale + 18, y + py * scale)
        c.line(x + px * scale, y + py * scale - 18, x + px * scale, y + py * scale + 18)
        c.setLineWidth(1.2)
    c.setFont("Helvetica", 10)
    c.drawString(x, y + 216, "TOP VIEW - 4 x DIA 6.5 THRU")
    c.line(x, y - 18, x + 320, y - 18)
    c.line(x, y - 24, x, y - 8)
    c.line(x + 320, y - 24, x + 320, y - 8)
    c.drawCentredString(x + 160, y - 34, "80.00")
    c.line(x - 20, y, x - 20, y + 200)
    c.line(x - 25, y, x - 10, y)
    c.line(x - 25, y + 200, x - 10, y + 200)
    c.drawString(x - 65, y + 95, "50.00")
    c.rect(x, 130, 320, 48)
    c.drawString(x, 190, "SIDE VIEW")
    c.drawString(x + 330, 150, "12.00")
    facts = [
        "Part number: PM-BRKT-010",
        f"Revision: {revision}",
        f"Material: {material or '[not provided]'}",
        f"Unit: {unit}",
        "Width: 80.00",
        "Height: 50.00",
        "Thickness: 12.00",
        "Hole diameter: 6.50",
        "Hole centers: (10,10), (70,10), (10,40), (70,40)",
        "Surface finish: Anodize",
        "Selected dimension tolerance: +/-0.10 mm",
    ]
    c.setFont("Helvetica", 9)
    for i, text in enumerate(facts):
        c.drawString(490, 450 - i * 23, text)
    c.setFont("Helvetica", 8)
    c.drawString(
        42,
        78,
        "Original training fixture. Simplified geometry; process times are separate demo assumptions.",
    )
    c.drawString(
        42,
        62,
        "Surface finish and dimensions are illustrative; no general tolerance standard is implied.",
    )
    c.drawString(
        42,
        46,
        "Sheet 1 / 1 | Units and title-block values must be confirmed before budget approval.",
    )
    c.save()


def main() -> None:
    DRAWINGS.mkdir(parents=True, exist_ok=True)
    index = []
    for case_id, label, quantity, revision, material, unit in CASES:
        suffix = (
            "missing-material"
            if material is None
            else "steel"
            if material == "Steel-S235"
            else "inch"
            if unit == "inch"
            else revision
        )
        pdf = DRAWINGS / f"PM-BRKT-010_{suffix}.pdf"
        if not any(item["drawing"] == pdf.relative_to(ROOT).as_posix() for item in index):
            drawing(pdf, revision, material, unit)
        folder = ROOT / "fixtures/dev" / case_id
        folder.mkdir(parents=True, exist_ok=True)
        rfq = folder / "rfq.md"
        rfq.write_text(
            f"# RFQ {case_id}\n\nSYNTHETIC DEMO DATA\n\n"
            f"Part number: PM-BRKT-010\nRequired revision: B\nQuantity: {quantity}\n"
            f"Currency: EUR\nPlant: DE-DEMO\nProduct family: mounting-bracket\n"
            f"Attachment: {pdf.name}\n\nPlease prepare an internal budget draft. "
            "Supplier availability, delivery date, capacity, freight, taxes and customer terms require separate confirmation.\n",
            encoding="utf-8",
            newline="\n",
        )
        index.append(
            {
                "id": case_id,
                "label": label,
                "origin_type": "synthetic",
                "split": "dev",
                "part_family_group": "PM-BRKT-010",
                "author": "RFQ Workbench",
                "license": "Original project fixture; synthetic demonstration only",
                "rfq": rfq.relative_to(ROOT).as_posix(),
                "drawing": pdf.relative_to(ROOT).as_posix(),
                "hashes": {
                    "rfq": hashlib.sha256(rfq.read_bytes()).hexdigest(),
                    "drawing": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                },
            }
        )
    write_json(ROOT / "fixtures/manifests/dev.json", {"schema_version": "1.0", "cases": index})
    write_json(
        ROOT / "fixtures/authoring/part-spec.json",
        {
            "origin_type": "synthetic",
            "runtime_access": False,
            "part_number": "PM-BRKT-010",
            "shape": "flat actuator mounting plate",
            "width_mm": 80,
            "height_mm": 50,
            "thickness_mm": 12,
            "holes": {"diameter_mm": 6.5, "centers_mm": [[10, 10], [70, 10], [10, 40], [70, 40]]},
        },
    )
    print(f"Generated {len(index)} development packages and 5 original PDFs; gold was not changed.")


if __name__ == "__main__":
    main()
