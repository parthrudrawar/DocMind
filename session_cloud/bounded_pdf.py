"""Original extraction, orchestrated sequentially inside a disposable OS-limited worker."""
from dataclasses import asdict
import math
from pathlib import Path
import shutil
import time

import fitz

from session_cloud.original_pdf import OriginalPageParser
from session_cloud.parse_contract import ParseFailure, validate_result


class BoundedPDFParser(OriginalPageParser):
    def __init__(self, limits):
        self.limits, self.ocr_count = limits, 0
        self.tesseract = shutil.which("tesseract") if limits.ocr_enabled else None

    def _ocr_page(self, page, page_number):
        # Original behavior preserves short digital text. An image-only/short-text
        # scanned page must never silently become a blank indexed page.
        if not page.get_images(full=False):
            return ""
        if not self.limits.ocr_enabled or not self.tesseract:
            raise ParseFailure("pdf_ocr_unavailable")
        self.ocr_count += 1
        if (self.ocr_count > self.limits.ocr_pages
                or math.ceil(page.rect.width * 2) * math.ceil(page.rect.height * 2) > self.limits.ocr_pixels):
            raise ParseFailure("pdf_ocr_limit")
        try:
            import pytesseract
            from PIL import Image

            pytesseract.pytesseract.tesseract_cmd = self.tesseract
            # Same scale, RGB rendering and psm=6 as the original Docker parser.
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), colorspace=fitz.csRGB, alpha=False)
            with Image.frombytes("RGB", (pix.width, pix.height), pix.samples) as image:
                text = pytesseract.image_to_string(image, config="--psm 6", timeout=self.limits.ocr_seconds)
            if not text.strip():
                raise ParseFailure("pdf_ocr_failed")
            return text
        except ImportError:
            raise ParseFailure("pdf_ocr_unavailable") from None
        except ParseFailure:
            raise
        except Exception:
            raise ParseFailure("pdf_ocr_failed") from None

    def parse(self, path):
        if not 0 < Path(path).stat().st_size <= self.limits.file_bytes:
            raise ParseFailure("pdf_invalid")
        try:
            with fitz.open(path) as document:
                if not document.is_pdf or document.is_repaired:
                    raise ParseFailure("pdf_invalid")
                if document.needs_pass:
                    raise ParseFailure("pdf_encrypted")
                if not 0 < len(document) <= self.limits.pages:
                    raise ParseFailure("pdf_pages")
                pages, total = [], 0
                for number in range(len(document)):
                    page = self._process_page(document[number], number + 1)
                    size = len(page.text.encode("utf-8"))
                    total += size
                    if size > self.limits.page_bytes or total > self.limits.text_bytes:
                        raise ParseFailure("pdf_text")
                    pages.append(asdict(page))
                # Whole document only. No page-level catch/substitution or partial output.
                validate_result({"version": 1, "pages": pages,
                                 "metrics": {"wall_seconds": 0, "cpu_seconds": 0, "peak_rss_bytes": 0}}, self.limits)
                return pages
        except ParseFailure:
            raise
        except MemoryError:
            raise ParseFailure("pdf_resource") from None
        except Exception:
            raise ParseFailure("pdf_invalid") from None


def peak_rss_bytes():
    import os

    if os.name != "nt":
        import resource
        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)
    import ctypes as c
    from ctypes import wintypes as w

    class Counters(c.Structure):
        _fields_ = [("cb", w.DWORD), ("faults", w.DWORD)] + [
            (name, c.c_size_t) for name in ("peak_ws", "ws", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]

    api = c.WinDLL("kernel32", use_last_error=True)
    api.GetCurrentProcess.restype = w.HANDLE
    psapi = c.WinDLL("psapi", use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, c.POINTER(Counters), w.DWORD]
    psapi.GetProcessMemoryInfo.restype = w.BOOL
    counters = Counters()
    counters.cb = c.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(api.GetCurrentProcess(), c.byref(counters), counters.cb):
        raise ParseFailure("pdf_worker")
    return int(counters.peak_ws)


def prepare_pdf(path, limits):
    start, cpu_start = time.monotonic(), time.process_time()
    pages = BoundedPDFParser(limits).parse(path)
    return {"version": 1, "pages": pages, "metrics": {
        "wall_seconds": round(time.monotonic() - start, 4),
        "cpu_seconds": round(time.process_time() - cpu_start, 4), "peak_rss_bytes": peak_rss_bytes()}}
