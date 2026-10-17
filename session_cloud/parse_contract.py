"""Bounded parser protocol; no deployment settings, identities or credentials."""
from dataclasses import asdict, dataclass
import math


ERRORS = {
    "pdf_invalid", "pdf_encrypted", "pdf_pages", "pdf_text", "pdf_blank", "pdf_table",
    "pdf_ocr_unavailable", "pdf_ocr_limit", "pdf_ocr_failed", "pdf_time", "pdf_resource", "pdf_worker",
}


class ParseFailure(Exception):
    def __init__(self, code):
        self.code = code if isinstance(code, str) and code in ERRORS else "pdf_worker"
        super().__init__(self.code)


@dataclass(frozen=True)
class ParseLimits:
    file_bytes: int = 100 * 1024 * 1024
    pages: int = 200
    page_bytes: int = 32_000
    text_bytes: int = 1_000_000
    output_bytes: int = 4 * 1024 * 1024
    memory_bytes: int = 512 * 1024 * 1024
    seconds: int = 120
    cpu_seconds: int = 60
    ocr_pages: int = 3
    ocr_pixels: int = 4_000_000
    ocr_seconds: int = 15
    ocr_enabled: bool = False

    def __post_init__(self):
        ceilings = (100 * 1024 * 1024, 200, 32_000, 1_000_000, 4 * 1024 * 1024,
                    512 * 1024 * 1024, 120, 60, 3, 4_000_000, 15)
        for value, ceiling in zip(list(asdict(self).values())[:-1], ceilings):
            if type(value) is not int or not 0 < value <= ceiling:
                raise ValueError("Parser limit is invalid")
        if type(self.ocr_enabled) is not bool:
            raise ValueError("Parser OCR policy is invalid")


def validate_result(result, limits):
    """Validate bounded data before it enters the workspace DB; never log it."""
    if not isinstance(result, dict) or type(result.get("version")) is not int or result["version"] != 1:
        raise ParseFailure("pdf_worker")
    if "error" in result:
        raise ParseFailure(result["error"])
    pages, metrics = result.get("pages"), result.get("metrics")
    if not isinstance(pages, list) or not 0 < len(pages) <= limits.pages or not isinstance(metrics, dict):
        raise ParseFailure("pdf_worker")
    total = 0
    for number, page in enumerate(pages, 1):
        if (not isinstance(page, dict) or type(page.get("page_number")) is not int
                or page["page_number"] != number or not isinstance(page.get("text"), str)
                or type(page.get("is_ocr")) is not bool or type(page.get("has_tables")) is not bool
                or type(page.get("word_count")) is not int):
            raise ParseFailure("pdf_worker")
        for field in ("paragraphs", "table_blocks"):
            if (not isinstance(page.get(field), list) or len(page[field]) > 1000
                    or any(not isinstance(part, str) for part in page[field])):
                raise ParseFailure("pdf_worker")
        expected = "\n\n".join(page["paragraphs"] + page["table_blocks"]).strip()
        size = len(page["text"].encode("utf-8"))
        total += size
        if (page["text"] != expected or size > limits.page_bytes or total > limits.text_bytes
                or page["word_count"] != len(page["text"].split())):
            raise ParseFailure("pdf_text")
    if not any(page["text"].strip() for page in pages):
        raise ParseFailure("pdf_blank")
    if (set(metrics) != {"wall_seconds", "cpu_seconds", "peak_rss_bytes"}
            or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in metrics.values())):
        raise ParseFailure("pdf_worker")
    return result
