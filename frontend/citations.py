import re
from html import escape
from typing import Any

import mistune

_VISIBLE_MARKER = re.compile(r"\[(?P<bracket>\d+)\]|【(?P<provider>[^】]*)】")
_PROVIDER_REFERENCE = re.compile(r"\s*(\d+)(?:†L\d+(?:-L?\d+)?)?\s*")
_HTML_BREAK = re.compile(r"<br\s*/?>", flags=re.IGNORECASE)
_TABLE_DIVIDER_CELL = re.compile(r"^:?-{3,}:?$")
_MARKDOWN = mistune.create_markdown(escape=True, plugins=["table", "strikethrough"])


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def normalize_citations(citations: Any) -> list[dict]:
    """Return valid citation dictionaries, deduplicated by source number."""
    if not isinstance(citations, list):
        return []

    normalized = []
    seen = set()
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        number = _positive_int(citation.get("number"))
        if number is None or number in seen:
            continue
        clean = dict(citation)
        clean["number"] = number
        normalized.append(clean)
        seen.add(number)
    return normalized


def _citation_map(citations: Any) -> dict[int, dict]:
    return {citation["number"]: citation for citation in normalize_citations(citations)}


def _page_label(citation: dict) -> str:
    page_start = _positive_int(citation.get("page_number"))
    page_end = _positive_int(citation.get("page_end"))
    if page_start is None:
        return ""
    if page_end is not None and page_end > page_start:
        return f"Pages {page_start}–{page_end}"
    return f"Page {page_start}"


def _source_title(citation: dict, number: int) -> str:
    return str(
        citation.get("filename") or citation.get("title") or f"Source {number}"
    ).strip()


def _supporting_excerpt(citation: dict) -> str:
    return str(
        citation.get("exact_quote") or citation.get("text_snippet") or ""
    ).strip()


def _marker_number(match: re.Match) -> int | None:
    bracket = match.group("bracket")
    if bracket is not None:
        return _positive_int(bracket)
    provider = _PROVIDER_REFERENCE.fullmatch(match.group("provider") or "")
    return _positive_int(provider.group(1)) if provider else None


def _inline_reference(number: int | None, citation: dict | None) -> str:
    if number is None:
        return (
            '<span class="citation-reference citation-missing" '
            'title="Citation details unavailable">[?]</span>'
        )
    if citation is None:
        return (
            '<span class="citation-reference citation-missing" '
            f'title="Source {number} details unavailable">[{number}]</span>'
        )

    title = escape(_source_title(citation, number), quote=True)
    page = escape(_page_label(citation), quote=True)
    excerpt = escape(_supporting_excerpt(citation), quote=True)
    page_html = f'<span class="inline-citation-meta">{page}</span>' if page else ""
    excerpt_html = (
        f'<span class="inline-citation-excerpt">“{excerpt}”</span>'
        if excerpt
        else '<span class="inline-citation-meta">Supporting excerpt unavailable.</span>'
    )
    return (
        f'<span class="inline-citation" data-citation-number="{number}" '
        f'tabindex="0" role="button" aria-label="Open Source {number}">'
        f'<span class="inline-citation-label">[{number}]</span>'
        '<span class="inline-citation-popover">'
        f'<span class="inline-citation-title">Source {number} · {title}</span>'
        f"{page_html}{excerpt_html}</span></span>"
    )


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    stripped = stripped.removeprefix("|")
    if stripped.endswith("|") and not stripped.endswith(r"\|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", stripped)]


def _is_table_divider(line: str) -> bool:
    cells = _split_table_row(line)
    return len(cells) >= 2 and all(_TABLE_DIVIDER_CELL.fullmatch(cell) for cell in cells)


def _join_table_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _repair_markdown_tables(text: str) -> str:
    """Normalize only explicit pipe tables without interpreting arbitrary prose."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    repaired = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if index + 1 >= len(lines) or "|" not in line or not _is_table_divider(lines[index + 1]):
            repaired.append(_HTML_BREAK.sub("\n", line))
            index += 1
            continue

        header = _split_table_row(_HTML_BREAK.sub(" · ", line))
        column_count = len(header)
        divider = _split_table_row(lines[index + 1])
        divider = (divider + ["---"] * column_count)[:column_count]
        repaired.extend((_join_table_row(header), _join_table_row(divider)))
        index += 2

        while index < len(lines):
            row = lines[index]
            if not row.strip() or "|" not in row:
                break
            row = re.sub(r"<br\s*/?>\s*[-*]\s+", " • ", row, flags=re.IGNORECASE)
            cells = _split_table_row(_HTML_BREAK.sub(" · ", row))
            if len(cells) < column_count:
                cells.extend([""] * (column_count - len(cells)))
            elif len(cells) > column_count:
                cells = cells[: column_count - 1] + ["; ".join(cells[column_count - 1 :])]
            repaired.append(_join_table_row(cells))
            index += 1

    return "\n".join(repaired)


def _stable_stream_prefix(text: str) -> str:
    """Hold an unfinished trailing table until a blank line closes the block."""
    lines = text.split("\n")
    for index in range(len(lines) - 1):
        if (
            "|" in lines[index]
            and _is_table_divider(lines[index + 1])
            and not any(not line.strip() for line in lines[index + 2 :])
        ):
            return "\n".join(lines[:index]).rstrip()
    return text


def render_answer_html(answer: Any, citations: Any, *, streaming: bool = False) -> str:
    """Render safe Markdown and inject trusted, sanitized citation controls."""
    text = _repair_markdown_tables(str(answer or ""))
    if streaming:
        text = _stable_stream_prefix(text)
    by_number = _citation_map(citations)
    markdown_parts = []
    replacements = {}
    cursor = 0
    for index, match in enumerate(_VISIBLE_MARKER.finditer(text)):
        markdown_parts.append(text[cursor : match.start()])
        number = _marker_number(match)
        token = f"DOCUMINDCITATIONTOKEN{index}END"
        markdown_parts.append(token)
        replacements[token] = _inline_reference(number, by_number.get(number))
        cursor = match.end()
    markdown_parts.append(text[cursor:])

    rendered = _MARKDOWN("".join(markdown_parts))
    for token, citation_html in replacements.items():
        rendered = rendered.replace(token, citation_html)
    return rendered.strip()


def render_source_card_html(citation: dict) -> str:
    """Render one sanitized source card using only returned retrieval metadata."""
    number = _positive_int(citation.get("number"))
    if number is None:
        return ""

    title = escape(_source_title(citation, number), quote=True)
    page = escape(_page_label(citation), quote=True)
    section = escape(str(citation.get("section") or "").strip(), quote=True)
    excerpt = escape(_supporting_excerpt(citation), quote=True)
    metadata = " · ".join(value for value in (page, section) if value)
    metadata_html = f'<div class="citation-meta">{metadata}</div>' if metadata else ""
    excerpt_html = (
        f'<div class="citation-exact-quote">“{excerpt}”</div>'
        if excerpt
        else '<div class="citation-meta">Supporting excerpt unavailable.</div>'
    )
    return (
        f'<div class="citation-card" id="source-{number}">'
        '<div class="citation-card-heading">'
        f'<span class="citation-number">[{number}]</span>'
        f'<span class="citation-title">{title}</span></div>'
        f"{metadata_html}{excerpt_html}</div>"
    )
