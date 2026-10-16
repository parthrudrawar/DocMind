from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, List, Optional

if TYPE_CHECKING:
    from app.ingestion.pdf_parser import ParsedPage


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    page_number: int
    level: int
    section: str
    text: str
    word_count: int
    token_count: int
    page_end: int
    chunk_index: Optional[int] = None
    parent_chunk_id: Optional[str] = None
    chunk_type: str = "text"
    is_table: bool = False
    is_ocr: bool = False
    indexable: bool = True


@dataclass
class _Unit:
    text: str
    page_number: int
    section: str
    is_table: bool = False
    is_ocr: bool = False


_SECTION_PATTERN = re.compile(
    r"^(abstract|introduction|background|related work|literature review|"
    r"methods?|methodology|materials?|experimental|experiments?|"
    r"results?|findings?|evaluation|discussion|conclusion|"
    r"summary|overview|references?|bibliography|appendix|"
    r"acknowledgements?|contributions?)[\s:\.\-]*$",
    re.IGNORECASE,
)

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


class Chunker:
    """Build bounded parent chunks and token-aware retrieval children."""

    def __init__(
        self,
        token_counter: Optional[Callable[[str], int]] = None,
        target_tokens: int | None = None,
        overlap_tokens: int | None = None,
        parent_tokens: int | None = None,
    ):
        # Explicit callers can reuse chunking without loading deployment settings,
        # OCR discovery, credentials, database paths or embedding singletons.
        if target_tokens is None or overlap_tokens is None or parent_tokens is None:
            from app.config import settings

            target_tokens = settings.CHUNK_TARGET_TOKENS if target_tokens is None else target_tokens
            overlap_tokens = settings.CHUNK_OVERLAP_TOKENS if overlap_tokens is None else overlap_tokens
            parent_tokens = settings.PARENT_CHUNK_TOKENS if parent_tokens is None else parent_tokens
        if target_tokens <= 0:
            raise ValueError("target_tokens must be positive")
        if overlap_tokens < 0 or overlap_tokens >= target_tokens:
            raise ValueError("overlap_tokens must be between 0 and target_tokens")
        if parent_tokens < target_tokens:
            raise ValueError("parent_tokens must be at least target_tokens")

        self._count_tokens = token_counter or self._fallback_token_count
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens
        self.parent_tokens = parent_tokens

    def chunk_document(self, doc_id: str, pages: List[ParsedPage]) -> List[Chunk]:
        units = self._collect_units(pages)
        if not units:
            return []

        chunks: List[Chunk] = [self._document_summary(doc_id, units)]
        child_index = 0

        for section_units in self._group_by_section(units):
            atoms = self._to_atoms(section_units, self.target_tokens)
            for parent_atoms in self._pack_without_overlap(atoms, self.parent_tokens):
                parent = self._make_chunk(
                    doc_id=doc_id,
                    level=2,
                    section=parent_atoms[0].section,
                    units=parent_atoms,
                    chunk_type="parent",
                    indexable=False,
                )
                chunks.append(parent)

                for child_atoms in self._pack_children(parent_atoms):
                    child = self._make_chunk(
                        doc_id=doc_id,
                        level=3,
                        section=parent.section,
                        units=child_atoms,
                        parent_chunk_id=parent.chunk_id,
                        chunk_index=child_index,
                        chunk_type=(
                            "table" if all(unit.is_table for unit in child_atoms) else "text"
                        ),
                        indexable=True,
                    )
                    chunks.append(child)
                    child_index += 1

        return chunks

    def _collect_units(self, pages: List[ParsedPage]) -> List[_Unit]:
        units: List[_Unit] = []
        current_section = "main"

        for page in pages:
            paragraphs = page.paragraphs or self._fallback_paragraphs(page.text)
            for paragraph in paragraphs:
                lines = [" ".join(line.split()) for line in paragraph.splitlines() if line.strip()]
                if lines and _SECTION_PATTERN.fullmatch(lines[0]):
                    current_section = lines.pop(0).lower().rstrip(":.- ")
                cleaned = " ".join(lines)
                if not cleaned:
                    continue
                units.append(
                    _Unit(
                        text=cleaned,
                        page_number=page.page_number,
                        section=current_section,
                        is_ocr=page.is_ocr,
                    )
                )

            for table in page.table_blocks:
                cleaned = table.strip()
                if cleaned:
                    units.append(
                        _Unit(
                            text=cleaned,
                            page_number=page.page_number,
                            section=current_section,
                            is_table=True,
                            is_ocr=page.is_ocr,
                        )
                    )

        return units

    @staticmethod
    def _fallback_paragraphs(text: str) -> List[str]:
        return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]

    @staticmethod
    def _group_by_section(units: List[_Unit]) -> List[List[_Unit]]:
        groups: List[List[_Unit]] = []
        for unit in units:
            if not groups or groups[-1][-1].section != unit.section:
                groups.append([unit])
            else:
                groups[-1].append(unit)
        return groups

    def _to_atoms(self, units: List[_Unit], max_tokens: int) -> List[_Unit]:
        atoms: List[_Unit] = []
        for unit in units:
            if unit.is_table:
                atoms.extend(self._split_table(unit, max_tokens))
                continue

            sentences = [
                sentence.strip()
                for sentence in _SENTENCE_BOUNDARY.split(unit.text)
                if sentence.strip()
            ] or [unit.text]

            for sentence in sentences:
                atoms.extend(self._split_oversized_text(unit, sentence, max_tokens))
        return atoms

    def _split_table(self, unit: _Unit, max_tokens: int) -> List[_Unit]:
        rows = [row.strip() for row in unit.text.splitlines() if row.strip()]
        if not rows:
            return []
        if self._tokens(unit.text) <= max_tokens:
            return [unit]
        if len(rows) == 1:
            return self._split_oversized_text(unit, rows[0], max_tokens)

        header = rows[0]
        result: List[_Unit] = []
        current = [header]
        for row in rows[1:]:
            if self._tokens(row) > max_tokens:
                if len(current) > 1:
                    result.append(self._copy_unit(unit, "\n".join(current)))
                result.extend(self._split_oversized_text(unit, row, max_tokens))
                current = [header]
                continue
            candidate = "\n".join(current + [row])
            if len(current) > 1 and self._tokens(candidate) > max_tokens:
                result.append(self._copy_unit(unit, "\n".join(current)))
                current = [header, row]
            else:
                current.append(row)
        if len(current) > 1 or not result:
            result.append(self._copy_unit(unit, "\n".join(current)))
        return result

    def _split_oversized_text(
        self,
        source: _Unit,
        text: str,
        max_tokens: int,
    ) -> List[_Unit]:
        if self._tokens(text) <= max_tokens:
            return [self._copy_unit(source, text)]

        words = text.split()
        pieces: List[_Unit] = []
        start = 0
        while start < len(words):
            low, high = start + 1, len(words)
            best = start + 1
            while low <= high:
                middle = (low + high) // 2
                candidate = " ".join(words[start:middle])
                if self._tokens(candidate) <= max_tokens:
                    best = middle
                    low = middle + 1
                else:
                    high = middle - 1
            pieces.append(self._copy_unit(source, " ".join(words[start:best])))
            start = best
        return pieces

    def _pack_without_overlap(
        self,
        units: List[_Unit],
        token_limit: int,
    ) -> List[List[_Unit]]:
        groups: List[List[_Unit]] = []
        current: List[_Unit] = []
        for unit in units:
            if current and self._tokens(self._join(current + [unit])) > token_limit:
                groups.append(current)
                current = []
            current.append(unit)
        if current:
            groups.append(current)
        return groups

    def _pack_children(self, units: List[_Unit]) -> List[List[_Unit]]:
        groups: List[List[_Unit]] = []
        current: List[_Unit] = []

        for unit in units:
            if current and self._tokens(self._join(current + [unit])) > self.target_tokens:
                groups.append(current)
                current = self._overlap_tail(current)
                while current and self._tokens(self._join(current + [unit])) > self.target_tokens:
                    current.pop(0)
            current.append(unit)

        if current:
            groups.append(current)
        return groups

    def _overlap_tail(self, units: List[_Unit]) -> List[_Unit]:
        if self.overlap_tokens == 0:
            return []
        tail: List[_Unit] = []
        for unit in reversed(units):
            candidate = [unit] + tail
            if tail and self._tokens(self._join(candidate)) > self.overlap_tokens:
                break
            tail = candidate
            if self._tokens(self._join(tail)) >= self.overlap_tokens:
                break
        return tail

    def _document_summary(self, doc_id: str, units: List[_Unit]) -> Chunk:
        atoms = self._to_atoms(units, self.parent_tokens)
        summary_atoms = self._pack_without_overlap(atoms, self.parent_tokens)[0]
        return self._make_chunk(
            doc_id=doc_id,
            level=1,
            section="full_document",
            units=summary_atoms,
            chunk_type="summary",
            indexable=False,
        )

    def _make_chunk(
        self,
        doc_id: str,
        level: int,
        section: str,
        units: List[_Unit],
        chunk_type: str,
        indexable: bool,
        parent_chunk_id: Optional[str] = None,
        chunk_index: Optional[int] = None,
    ) -> Chunk:
        text = self._join(units).strip()
        pages = [unit.page_number for unit in units]
        return Chunk(
            chunk_id=str(uuid.uuid4()),
            doc_id=doc_id,
            page_number=min(pages),
            page_end=max(pages),
            level=level,
            section=section,
            text=text,
            word_count=len(text.split()),
            token_count=self._tokens(text),
            chunk_index=chunk_index,
            parent_chunk_id=parent_chunk_id,
            chunk_type=chunk_type,
            is_table=any(unit.is_table for unit in units),
            is_ocr=any(unit.is_ocr for unit in units),
            indexable=indexable,
        )

    def _tokens(self, text: str) -> int:
        return max(1, int(self._count_tokens(text))) if text else 0

    @staticmethod
    def _join(units: List[_Unit]) -> str:
        separator = "\n" if units and all(unit.is_table for unit in units) else " "
        return separator.join(unit.text for unit in units)

    @staticmethod
    def _copy_unit(source: _Unit, text: str) -> _Unit:
        return _Unit(
            text=text,
            page_number=source.page_number,
            section=source.section,
            is_table=source.is_table,
            is_ocr=source.is_ocr,
        )

    @staticmethod
    def _fallback_token_count(text: str) -> int:
        return len(re.findall(r"\w+|[^\w\s]", text, re.UNICODE))
