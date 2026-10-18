"""Real PDF tables -> original native RAG -> mocked Groq SSE -> safe HTML."""
from io import BytesIO
from types import SimpleNamespace

import fitz
import pytest
import test_generation
import test_session_retrieval

from frontend.citations import render_answer_html
from session_cloud.adapter import LocalAPI
from session_cloud.generation import GenerationService
from session_cloud.original_prompts import _messages_for_state
from session_cloud.runtime import SessionError

model_cache = test_session_retrieval.model_cache
environment = test_session_retrieval.environment
A, B = test_session_retrieval.A, test_session_retrieval.B
ROWS = [("2023", "1,250.50", "-2.75"), ("2024", "1,450.00", "3.25")]


def financial_pdf():
    with fitz.open() as pdf:
        cover = pdf.new_page()
        cover.insert_text((45, 60), "Amber annual results. Revenue is reported in USD millions and margin in percent.")
        page = pdf.new_page()
        page.insert_text((45, 60), "Audited annual revenue and operating margin. Figures retain their reported decimal precision.")
        for x in (40, 150, 350, 530):
            page.draw_line((x, 110), (x, 200))
        for y in (110, 140, 170, 200):
            page.draw_line((40, y), (530, y))
        for y, row in zip((130, 160, 190), [("Year", "Revenue (USD millions)", "Margin (%)"), *ROWS]):
            for x, value in zip((50, 160, 360), row):
                page.insert_text((x, y), value, fontsize=9)
        return pdf.tobytes()


def test_real_pdf_table_values_retrieval_reranking_citations_and_stream_rendering(environment, tmp_path):
    store, _, _ = environment
    ref = store.open(A)
    data = financial_pdf()
    fixture = tmp_path / "public-financial-fixture.pdf"
    fixture.write_bytes(data)
    with fitz.open(stream=data, filetype="pdf") as pdf:
        for number, page in enumerate(pdf, 1):
            page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25)).save(tmp_path / f"table-page-{number}.png")
    indexed = store.ingest_upload(A, ref, "public-financial-fixture.pdf", BytesIO(data))
    doc = indexed["id"]
    assert indexed["status"] == "indexed"
    assert store.ingest_upload(A, ref, "renamed.pdf", BytesIO(data))["duplicate"]
    assert store._uploads == store._preparations == 1
    pages = store.parsed_pages(A, ref, doc)
    assert [p["page_number"] for p in pages] == [1, 2]
    table = "\n".join(pages[1]["table_blocks"])
    assert pages[1]["has_tables"] and "Revenue (USD millions)" in table
    for row in ROWS:
        assert " | ".join(row) in table
    other = store.open(B)
    assert store.documents(B, other) == []
    with pytest.raises(SessionError):
        store.retrieve(B, other, "annual revenue", [doc])
    query = "List the annual revenue and margin as a Markdown table."
    hits = store.retrieve(A, ref, query, [doc])
    assert hits and any("1,250.50" in hit["text"] and hit["page_number"] <= 2 <= hit["page_end"] for hit in hits)
    context = store.prepare_answer_context(A, ref, query, [doc])
    citation = next(i for i, c in enumerate(context["chunks"], 1)
        if "1,250.50" in c["text"] and "1,450.00" in c["text"] and c["page_number"] <= 2 <= c["page_end"])
    assert all(c["doc_id"] == doc for c in context["chunks"])
    messages = _messages_for_state({"user_query": query, "aggregated_evidence": context["aggregated_evidence"]})
    assert "decimal precision" in messages[0]["content"]
    answer = ("| Year | Revenue (USD millions) | Margin (%) |\n| --- | --- | --- |\n" +
        "\n".join(f"| {year} | {revenue} [{citation}] | {margin} [{citation}] |" for year, revenue, margin in ROWS))
    provider, requests, _ = test_generation.provider([test_generation.wire(answer), test_generation.wire(test_generation.FOLLOW)])
    store.daily_quota = SimpleNamespace(reserve=lambda capability, owner: None)
    store.generation = GenerationService(store, provider)
    chat = store.new_chat(A, ref)
    api = LocalAPI(store, A, ref, chat, [doc])
    pieces, stages = [], []
    completed, metadata = api.parse_stream(api.query_stream(query, ref, chat, turn_id="table-answer"),
        on_token=pieces.append, on_stage=stages.append)
    assert completed == answer and len(pieces) > 1
    assert {s["stage"] for s in stages} >= {"searching", "reranking", "evidence", "generating", "citations"}
    assert metadata["completed"] and len(requests) == 2
    assert all(c["doc_id"] == doc and c["page_number"] <= 2 <= c["page_end"] and c["filename"] == "public-financial-fixture.pdf"
               for c in metadata["citations"])
    assert all("1,250.50" in c["exact_quote"] or "1,450.00" in c["exact_quote"] for c in metadata["citations"])
    rendered = render_answer_html(completed, metadata["citations"])
    assert "<table>" in rendered and rendered.count("<tr>") == 3
    assert "inline-citation" in rendered
    for row in ROWS:
        for value in row:
            assert value in rendered
    partial = render_answer_html(answer[:len(answer)//2], [], streaming=True)
    assert partial.count("<table>") == partial.count("</table>")
    attack = render_answer_html(answer + '\n<script>alert("dummy")</script>', metadata["citations"])
    assert "<script>" not in attack and "&lt;script&gt;" in attack
    assert len(store.history(A, ref, chat)) == 2
