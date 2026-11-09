"""Bounded local document ingestion and page previews; never reads evaluation gold.

Text lines have stable parser-issued IDs. PDF evidence is explicitly page-level:
we do not invent region coordinates from a text extraction order.
"""

import hashlib
from io import BytesIO
from pathlib import Path
from threading import Lock
from uuid import uuid4

import pypdfium2 as pdfium
from pypdf import PdfReader

from rfq_workbench.errors import DomainError

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_PAGES = 3
MAX_TEXT = 60_000
PDFIUM_LOCK = Lock()  # PDFium is not thread safe, including across documents.


def ingest(root: Path, run_id: str, kind: str, filename: str, data: bytes) -> tuple[dict, list]:
    if not data or len(data) > MAX_FILE_BYTES:
        raise DomainError("FILE_SIZE", "Upload a nonempty file no larger than 5 MiB.")
    suffix = Path(filename).suffix.lower()
    if (kind == "drawing" and suffix != ".pdf") or suffix not in (".pdf", ".md", ".txt"):
        raise DomainError("FILE_TYPE", "Drawing must be PDF; RFQ supports PDF, MD or TXT.")
    doc_id = uuid4().hex
    images: list[bytes] = []
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise DomainError("INVALID_PDF", "File is not a supported PDF.")
        try:
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted or not 1 <= len(reader.pages) <= MAX_PAGES:
                raise DomainError("PDF_LIMIT", "Use an unencrypted PDF with 1–3 pages.")
            texts = [(page.extract_text() or "") for page in reader.pages]
            with PDFIUM_LOCK:
                pdf = pdfium.PdfDocument(data)
                try:
                    for number in range(len(pdf)):
                        page = pdf[number]
                        try:
                            width, height = page.get_size()
                            if min(width, height) <= 0 or max(width, height) > 5000:
                                raise DomainError(
                                    "PDF_LIMIT", "PDF page dimensions exceed demo limits."
                                )
                            bitmap = page.render(scale=min(1.5, 1600 / max(width, height)))
                            try:
                                with BytesIO() as buffer:
                                    bitmap.to_pil().save(buffer, format="PNG")
                                    images.append(buffer.getvalue())
                            finally:
                                bitmap.close()
                        finally:
                            page.close()
                finally:
                    pdf.close()
        except DomainError:
            raise
        except Exception as exc:
            # Parser error messages may contain supplied document content.
            raise DomainError("INVALID_PDF", "PDF could not be parsed or rendered.") from exc
    else:
        try:
            texts = [data.decode("utf-8-sig")]
        except UnicodeDecodeError as exc:
            raise DomainError("INVALID_TEXT", "RFQ text must use UTF-8 encoding.") from exc
    if sum(len(text) for text in texts) > MAX_TEXT:
        raise DomainError("TEXT_LIMIT", "Document exceeds the 60,000 character demo limit.")

    folder = root / "uploads" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{doc_id}{suffix}").write_bytes(data)
    sources = []
    pages = []
    for number, page_text in enumerate(texts, start=1):
        page_id = f"{doc_id}:p{number}"
        sources.append(
            {
                "id": page_id,
                "document_id": doc_id,
                "page": number,
                "text": page_text,
                "bbox": None,
                "locator": "page",
            }
        )
        for line_number, line in enumerate(page_text.splitlines(), start=1):
            if line.strip():
                sources.append(
                    {
                        "id": f"{page_id}:l{line_number}",
                        "document_id": doc_id,
                        "page": number,
                        "text": line.strip(),
                        "bbox": None,
                        "locator": "page" if suffix == ".pdf" else "text",
                    }
                )
        pages.append({"number": number, "text": page_text, "has_preview": bool(images)})
        if images:
            (folder / f"{doc_id}.page{number}.png").write_bytes(images[number - 1])
    return {
        "id": doc_id,
        "kind": kind,
        "name": Path(filename.replace("\\", "/")).name,
        "suffix": suffix,
        "sha256": hashlib.sha256(data).hexdigest(),
        "pages": pages,
    }, sources


def asset_path(root: Path, body: dict, doc_id: str, page: int) -> Path:
    document = next((d for d in body["documents"] if d["id"] == doc_id), None)
    if not document or page < 1 or page > len(document["pages"]):
        raise DomainError("NOT_FOUND", "Document page is not part of this task.", 404)
    if not document["pages"][page - 1]["has_preview"]:
        raise DomainError("NO_PREVIEW", "This document is a text file.", 404)
    return root / "uploads" / body["id"] / f"{doc_id}.page{page}.png"
