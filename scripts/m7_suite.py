"""Freeze verification and extraction scoring for the M7 synthetic holdout."""

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "evals/frozen/m7"
GOLD = ROOT / "evals/gold/m7.json"
PRICING = ROOT / "config/model-pricing.json"
LABELS = (
    "Part number",
    "Required revision",
    "Quantity",
    "Currency",
    "Part number",
    "Revision",
    "Material",
    "Unit",
    "Width",
    "Height",
    "Thickness",
    "Hole diameter",
    "Surface finish",
)
DIMENSIONS = {"width_mm", "height_mm", "thickness_mm", "hole_diameter_mm"}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class Suite:
    name: str
    manifest_hash: str
    cases: list[dict]
    gold: dict
    protocol: dict

    @classmethod
    def load(cls) -> "Suite":
        manifest_path = FROZEN / "manifest.json"
        manifest = read_json(manifest_path)
        ids = {f"F{i:02d}" for i in range(1, 13)}
        expected = {
            FROZEN / "cases" / case / name
            for case in ids
            for name in ("rfq.txt", "drawing.pdf", "manufacturing.json", "knowledge.json")
        } | {
            FROZEN / n
            for n in (
                "cases.json",
                "protocol.json",
                "prompt-injection.txt",
                "quantity-addendum.txt",
            )
        }
        observed = {p for p in FROZEN.rglob("*") if p.is_file()} - {manifest_path}
        required = {p.relative_to(ROOT).as_posix() for p in expected | {GOLD, PRICING}}
        if set(manifest["sha256"]) != required or observed != expected:
            raise ValueError("Frozen inventory changed")
        for name, digest in manifest["sha256"].items():
            path = ROOT / name
            if not path.resolve().is_relative_to(ROOT) or sha256(path) != digest:
                raise ValueError(f"Frozen SHA-256 mismatch: {name}")

        cases, gold, protocol = (
            read_json(FROZEN / "cases.json"),
            read_json(GOLD),
            read_json(FROZEN / "protocol.json"),
        )
        parts = {row["part_number"] for row in cases}
        dev = read_json(ROOT / "fixtures/manifests/dev.json")["cases"]
        dev_parts = {row["part_family_group"] for row in dev}
        dev_hashes = {row["hashes"]["drawing"] for row in dev}
        frozen_hashes = {sha256(FROZEN / "cases" / row["id"] / "drawing.pdf") for row in cases}
        if (
            len(cases) != protocol["primary_cases"]
            or {row["id"] for row in cases} != ids
            or set(gold["cases"]) != ids
            or len(parts) != len(ids)
            or parts & dev_parts
            or len(frozen_hashes) != len(ids)
            or frozen_hashes & dev_hashes
            or sum("quote" in row for row in gold["cases"].values()) != protocol["positive_cases"]
        ):
            raise ValueError("Holdout identity, grouping or coverage does not match the protocol")
        return cls(manifest["suite"], sha256(manifest_path), cases, gold, protocol)


def same_value(field: str, actual: str, expected: str) -> bool:
    name = field.split(".")[1]
    numeric = r"[0-9]+(?:\.[0-9]+)?" if name in DIMENSIONS else r"[0-9]+"
    if name in DIMENSIONS | {"quantity"} and all(
        re.fullmatch(numeric, v) for v in (actual, expected)
    ):
        return Decimal(actual) == Decimal(expected)
    return actual.strip().casefold() == expected.casefold()


def score_fields(run: dict, order: list[str], expected: list, drawing_page: int) -> list[dict]:
    """Score raw extraction before review. Page existence is not text support."""
    sources = {row["id"]: row for row in run["sources"]}
    documents = {row["id"]: row for row in run["documents"]}
    fields = []
    for key, label, want in zip(order, LABELS, expected, strict=True):
        value = run["fields"].get(key, {})
        actual = value.get("value")
        kind = key.split(".")[0]
        source = sources.get(value.get("source_id"), {})
        doc = documents.get(source.get("document_id"), {})
        same_page = doc.get("kind") == kind and source.get("page") == (
            drawing_page if kind == "drawing" else 1
        )
        correct = want is not None and actual is not None and same_value(key, actual, want)
        evidence = value.get("evidence", {}).get("status")
        supported = (
            correct
            and same_page
            and evidence == "TEXT_VERIFIED"
            and any(
                match and same_value(key, match[1], want)
                for line in source["text"].splitlines()
                if (match := re.fullmatch(re.escape(label) + r":\s*(.+)", line.strip(), re.I))
            )
        )
        visual = (
            correct
            and same_page
            and evidence == "VISUAL_REVIEW"
            and source.get("id") == f"{doc.get('id')}:p{drawing_page}"
            and kind == "drawing"
        )
        outcome = (
            ("correct_abstention" if want is None else "missed_value")
            if actual is None
            else "correct_value"
            if correct
            else "fabricated_missing"
            if want is None
            else "wrong_value"
        )
        fields.append(
            {
                "field": key,
                "expected": want,
                "actual": actual,
                "outcome": outcome,
                "evidence_status": evidence,
                "source_page": source.get("page") if same_page else None,
                "evidence_grade": "verified_text"
                if supported
                else "correct_visual_page"
                if visual
                else "unsupported"
                if actual is not None
                else "no_claim",
            }
        )
    return fields
