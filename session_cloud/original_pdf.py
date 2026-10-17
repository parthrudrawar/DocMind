"""Original page/table extraction helpers with strict cloud failure handling.

Source SHA256: EEA8C63CC4D0199CA367633FF5B6EC189FA4515E323A58F9AD27363C56EE3245
The page/table algorithms are retained; OCR dispatch and parse orchestration
are supplied by bounded_pdf.py. Table failures propagate, and image blocks are
not converted into text. The Docker reference is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import List

import fitz
from session_cloud.parse_contract import ParseFailure

logger = logging.getLogger(__name__)


@dataclass
class ParsedPage:
    page_number: int
    text: str
    has_tables: bool
    is_ocr: bool
    word_count: int
    paragraphs: List[str] = field(default_factory=list)
    table_blocks: List[str] = field(default_factory=list)



class OriginalPageParser:
    MIN_CHARS_FOR_DIGITAL = 50

    def _process_page(self, page: fitz.Page, page_number: int) -> ParsedPage:
        digital_text = page.get_text("text").strip()
        text = digital_text
        is_ocr = False

        if len(text) < self.MIN_CHARS_FOR_DIGITAL:
            logger.info(f"[pdf_parser] Page {page_number}: running OCR")
            ocr_text = self._ocr_page(page, page_number).strip()
            if ocr_text:
                text = ocr_text
                is_ocr = True
            elif digital_text:
                logger.warning(
                    "[pdf_parser] Page %s: OCR produced no text; preserving "
                    "the short digital-text extraction",
                    page_number,
                )

        table_blocks, table_bboxes = self._extract_tables(page)
        if is_ocr:
            paragraphs = self._split_paragraphs(text)
        else:
            paragraphs = self._extract_paragraphs(page, table_bboxes)
            if not paragraphs:
                paragraphs = self._split_paragraphs(text)

        # Keep a compatibility page-level representation while preserving
        # paragraphs and tables separately for structure-aware chunking.
        combined_text = "\n\n".join(paragraphs + table_blocks).strip()

        return ParsedPage(
            page_number=page_number,
            text=combined_text,
            has_tables=bool(table_blocks),
            is_ocr=is_ocr,
            word_count=len(combined_text.split()),
            paragraphs=paragraphs,
            table_blocks=table_blocks,
        )

    def _extract_paragraphs(
        self,
        page: fitz.Page,
        table_bboxes: List[tuple],
    ) -> List[str]:
        """Extract ordered text blocks, excluding blocks contained in tables."""
        paragraphs = []
        for block in sorted(page.get_text("blocks"), key=lambda b: (b[1], b[0])):
            if len(block) > 6 and block[6] != 0:
                continue
            bbox = tuple(block[:4])
            if any(self._mostly_overlaps(bbox, table_bbox) for table_bbox in table_bboxes):
                continue
            lines = [" ".join(line.split()) for line in str(block[4]).splitlines()]
            text = "\n".join(line for line in lines if line)
            if text:
                paragraphs.append(text)
        return paragraphs

    @staticmethod
    def _split_paragraphs(text: str) -> List[str]:
        paragraphs = []
        for part in text.replace("\r", "").split("\n\n"):
            cleaned = " ".join(part.split())
            if cleaned:
                paragraphs.append(cleaned)
        return paragraphs

    @staticmethod
    def _mostly_overlaps(block_bbox: tuple, table_bbox: tuple) -> bool:
        x0 = max(block_bbox[0], table_bbox[0])
        y0 = max(block_bbox[1], table_bbox[1])
        x1 = min(block_bbox[2], table_bbox[2])
        y1 = min(block_bbox[3], table_bbox[3])
        if x1 <= x0 or y1 <= y0:
            return False
        intersection = (x1 - x0) * (y1 - y0)
        block_area = max((block_bbox[2] - block_bbox[0]) * (block_bbox[3] - block_bbox[1]), 1)
        return intersection / block_area >= 0.5

    def _extract_tables(self, page: fitz.Page) -> tuple[List[str], List[tuple]]:
        try:
            tables = page.find_tables()
            if not tables.tables:
                return [], []
            blocks = []
            bboxes = []
            for table in tables.tables:
                rows = []
                for row in table.extract():
                    rows.append(" | ".join(str(c).strip() if c else "" for c in row))
                table_text = "\n".join(row for row in rows if row.strip(" |"))
                if table_text:
                    blocks.append(table_text)
                    bboxes.append(tuple(table.bbox))
            return blocks, bboxes
        except Exception:
            raise ParseFailure("pdf_table") from None
