import re
from collections.abc import Iterator


_CITATION_MARKER = re.compile(
    r"\[(?P<bracket>\d+)\]|"
    r"【\s*(?P<provider>\d+)(?:†L\d+(?:-L?\d+)?)?\s*】"
)


def iter_citation_markers(text: str) -> Iterator[tuple[int, int, int]]:
    """Yield positive citation number and character span for supported markers."""
    for match in _CITATION_MARKER.finditer(text or ""):
        raw_number = match.group("bracket") or match.group("provider")
        number = int(raw_number)
        if number > 0:
            yield number, match.start(), match.end()


def citation_numbers(text: str) -> set[int]:
    """Return unique citation numbers used in an answer."""
    return {number for number, _, _ in iter_citation_markers(text)}


def first_citation_position(text: str, citation_number: int) -> int | None:
    """Return the first supported marker position for one citation number."""
    return next(
        (
            start
            for number, start, _ in iter_citation_markers(text)
            if number == citation_number
        ),
        None,
    )
