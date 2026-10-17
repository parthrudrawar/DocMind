"""CSS copied from the original DocuMind frontend; no browser DOM scripts."""

CSS = r"""<style>
/* ── Base ── */
.stApp { background-color: #0E1117; color: #E2E8F0; }

[data-testid="stSidebar"] {
    background-color: #13151F;
    border-right: 1px solid #1E2535;
}

/* ── Hide default Streamlit chrome ── */
footer { visibility: hidden; }

/* ── App header ── */
.app-header {
    background: linear-gradient(135deg, #6C63FF 0%, #3B82F6 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-size: 1.9rem;
    font-weight: 800;
    letter-spacing: -0.5px;
}
.app-subheader {
    color: #4A5568;
    font-size: 0.85rem;
    margin-top: -6px;
}

/* ── Status badge ── */
.badge-online  { background:#1C3A2A; color:#4ADE80; border:1px solid #22543D;
                 border-radius:20px; padding:3px 10px; font-size:11px; }
.badge-offline { background:#3A1C1C; color:#FC8181; border:1px solid #542222;
                 border-radius:20px; padding:3px 10px; font-size:11px; }

/* ── Chat bubbles ── */
.user-bubble {
    background: linear-gradient(135deg, #6C63FF, #4F46E5);
    color: white;
    border-radius: 18px 18px 4px 18px;
    padding: 12px 16px;
    margin: 6px 0 6px auto;
    max-width: 78%;
    font-size: 0.9rem;
    line-height: 1.5;
    box-shadow: 0 4px 15px #6C63FF30;
}
.assistant-bubble {
    background: #1A1F2E;
    color: #CBD5E0;
    border-radius: 18px 18px 18px 4px;
    padding: 12px 16px;
    margin: 6px auto 6px 0;
    max-width: 85%;
    font-size: 0.9rem;
    line-height: 1.6;
    border-left: 3px solid #6C63FF;
}

/* ── Citation card ── */
.citation-card {
    background: #141824;
    border: 1px solid #1E2535;
    border-left: 4px solid #00D4FF;
    border-radius: 8px;
    padding: 10px 14px;
    margin: 5px 0;
    font-size: 0.82rem;
}
.citation-title { color: #00D4FF; font-weight: 600; }
.citation-meta  { color: #4A5568; font-size: 0.75rem; margin: 2px 0; }
.citation-text  { color: #718096; font-style: italic; margin-top: 6px; }
.citation-exact-quote {
    background: #0D1F2D;
    border-left: 3px solid #00D4FF;
    color: #E2E8F0;
    font-style: italic;
    font-size: 0.84rem;
    padding: 8px 12px;
    margin: 8px 0 4px 0;
    border-radius: 0 6px 6px 0;
    line-height: 1.6;
}
.citation-full-text {
    color: #4A5568;
    font-size: 0.78rem;
    margin-top: 4px;
    line-height: 1.5;
    padding: 4px 0;
}

/* �� Inline citation disclosures �� */
.inline-citation {
    display: inline-block;
    position: relative;
    margin: 0 2px;
    vertical-align: baseline;
    cursor: pointer;
}
.inline-citation-label {
    color: #00D4FF;
    background: #00D4FF14;
    border: 1px solid #00D4FF45;
    border-radius: 5px;
    padding: 1px 5px;
    font-size: 0.76rem;
    font-weight: 700;
    white-space: nowrap;
}
.inline-citation:focus { outline: none; }
.inline-citation:focus > .inline-citation-label,
.inline-citation:focus-within > .inline-citation-label {
    color: #E2E8F0;
    background: #6C63FF;
    border-color: #8B85FF;
}
.inline-citation-popover {
    display: none;
    position: relative;
    width: min(360px, 76vw);
    background: #101521;
    border: 1px solid #2B3650;
    border-left: 3px solid #00D4FF;
    border-radius: 8px;
    box-shadow: 0 12px 32px #00000080;
    padding: 10px 12px;
    text-align: left;
    margin-top: 7px;
    white-space: normal;
}
.inline-citation:focus > .inline-citation-popover,
.inline-citation:focus-within > .inline-citation-popover { display: block; }
.inline-citation-title {
    display: block;
    color: #E2E8F0;
    font-weight: 700;
    font-size: 0.8rem;
}
.inline-citation-meta {
    display: block;
    color: #7C8FAC;
    font-size: 0.72rem;
    margin-top: 3px;
}
.inline-citation-excerpt {
    display: block;
    color: #A0AEC0;
    font-size: 0.78rem;
    font-style: italic;
    line-height: 1.5;
    margin-top: 7px;
}
.assistant-bubble p { margin: 0 0 0.75rem; }
.assistant-bubble p:last-child { margin-bottom: 0; }
.assistant-bubble h1,
.assistant-bubble h2,
.assistant-bubble h3 {
    color: #E2E8F0;
    line-height: 1.3;
    margin: 0.9rem 0 0.45rem;
}
.assistant-bubble h1 { font-size: 1.25rem; }
.assistant-bubble h2 { font-size: 1.1rem; }
.assistant-bubble h3 { font-size: 1rem; }
.assistant-bubble ul,
.assistant-bubble ol { margin: 0.45rem 0 0.75rem 1.3rem; padding: 0; }
.assistant-bubble li { margin: 0.25rem 0; }
.assistant-bubble pre {
    max-width: 100%;
    overflow-x: auto;
    background: #0D111A;
    border: 1px solid #2B3650;
    border-radius: 7px;
    padding: 10px 12px;
}
.assistant-bubble code {
    color: #D6BCFA;
    background: #0D111A;
    border-radius: 4px;
    padding: 1px 4px;
}
.assistant-bubble pre code { padding: 0; }
.assistant-bubble table {
    display: block;
    width: 100%;
    max-width: 100%;
    overflow-x: auto;
    border-collapse: collapse;
    margin: 0.75rem 0;
    font-size: 0.82rem;
}
.assistant-bubble th,
.assistant-bubble td {
    border: 1px solid #2B3650;
    padding: 7px 9px;
    text-align: left;
    vertical-align: top;
    min-width: 110px;
}
.assistant-bubble th { color: #E2E8F0; background: #111827; }
.assistant-bubble tr:nth-child(even) td { background: #151B29; }
.chat-activity-row {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    color: #8B9BB4;
    font-size: 0.8rem;
    padding: 3px 0;
}
.chat-activity-row strong { color: #CBD5E0; }
.chat-activity-detail { color: #64748B; margin-left: 4px; }
.citation-reference {
    color: #00D4FF;
    font-size: 0.76rem;
    font-weight: 700;
    white-space: nowrap;
}
.citation-missing {
    color: #7C8FAC;
    border-bottom: 1px dotted #7C8FAC;
}
.citation-card-heading { display:flex; align-items:center; gap:9px; }
.citation-number {
    background: #00D4FF18;
    color: #00D4FF;
    font-weight: 700;
    font-size: 0.78rem;
    border-radius: 4px;
    padding: 2px 8px;
}

/* ── Confidence bar ── */
.evidence-badge {
    display: inline-block;
    border-radius: 999px;
    padding: 3px 10px;
    margin: 5px 0 7px;
    font-size: 0.76rem;
    font-weight: 700;
}
.evidence-strong { color:#4ADE80; background:#1C3A2A; border:1px solid #22543D; }
.evidence-moderate { color:#63B3ED; background:#162D3D; border:1px solid #234E67; }
.evidence-weak,
.evidence-limited,
.evidence-unrated { color:#FBBF24; background:#3A3018; border:1px solid #5B4A1C; }
.evidence-none { color:#FC8181; background:#3A1C1C; border:1px solid #542222; }

/* ── File status dots ── */
.dot-indexed    { color: #4ADE80; }
.dot-processing { color: #FBBF24; }
.dot-failed     { color: #FC8181; }
.dot-queued     { color: #718096; }

/* ── Follow-up buttons ── */
div[data-testid="stHorizontalBlock"] .stButton > button {
    background: #141824;
    color: #7C8FAC;
    border: 1px solid #1E2D45;
    border-radius: 20px;
    font-size: 0.78rem;
    padding: 4px 12px;
    transition: all 0.2s;
}
div[data-testid="stHorizontalBlock"] .stButton > button:hover {
    border-color: #6C63FF;
    color: #A5B4FC;
    background: #16192A;
}

/* ── Section labels ── */
.section-label {
    color: #4A5568;
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin: 14px 0 6px 0;
}

/* ── Project pill ── */
.project-pill {
    background: #1A1F35;
    border: 1px solid #2D3A5E;
    border-radius: 20px;
    padding: 3px 12px;
    font-size: 0.78rem;
    color: #818CF8;
    display: inline-block;
}
.guest-workspace-card {
    background: #161A28;
    border: 1px solid #2D3A5E;
    border-radius: 10px;
    padding: 10px 12px;
    margin: 5px 0 8px;
}
.guest-workspace-badge {
    display: inline-block;
    color: #A5B4FC;
    background: #6C63FF20;
    border: 1px solid #6C63FF55;
    border-radius: 999px;
    padding: 2px 8px;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
}
.guest-workspace-label {
    color: #E2E8F0;
    font-size: 0.9rem;
    font-weight: 700;
    margin-top: 7px;
}
.guest-privacy-notice {
    color: #7C8FAC;
    font-size: 0.74rem;
    line-height: 1.45;
    margin: 6px 0 2px;
}
.guest-state-card {
    max-width: 620px;
    margin: 12vh auto 0;
    background: #161A28;
    border: 1px solid #2D3A5E;
    border-left: 4px solid #6C63FF;
    border-radius: 12px;
    padding: 18px 20px;
}
.guest-state-title { color: #E2E8F0; font-size: 1rem; font-weight: 750; }
.guest-state-message { color: #8B9BB4; font-size: 0.86rem; margin-top: 5px; }

/* ── Doc row ── */
.doc-row {
    background: #141824;
    border: 1px solid #1E2535;
    border-radius: 8px;
    padding: 10px 14px;
    margin: 4px 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
}

/* ── Empty state ── */
.empty-state {
    text-align: center;
    padding: 40px 20px;
    color: #2D3748;
}
.empty-icon { font-size: 3rem; }

/* ── Divider ── */
.custom-divider {
    border: none;
    border-top: 1px solid #1E2535;
    margin: 12px 0;
}

/* ── Chat bubble animations ── */
.user-bubble {
    width: fit-content;
    margin-left: auto !important;
    animation: fadeSlideIn 0.25s ease;
}
.assistant-bubble {
    animation: fadeSlideIn 0.25s ease;
}
@keyframes fadeSlideIn {
    from { opacity: 0; transform: translateY(8px); }
    to   { opacity: 1; transform: translateY(0); }
}

/* ── Observable processing status ── */
.processing-indicator {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 14px;
    background: #1A1F2E;
    color: #A5B4FC;
    border-radius: 18px 18px 18px 4px;
    border-left: 3px solid #6C63FF;
    width: fit-content;
    margin: 8px 0;
    font-size: 0.82rem;
    font-weight: 600;
}
.processing-dots { display: inline-flex; gap: 4px; }
.processing-dots span {
    width: 6px; height: 6px;
    background: #6C63FF;
    border-radius: 50%;
    animation: typingBounce 1.2s infinite;
}
.processing-dots span:nth-child(2) { animation-delay: 0.2s; }
.processing-dots span:nth-child(3) { animation-delay: 0.4s; }
.stream-cursor {
    display: inline-block;
    width: 2px;
    height: 1em;
    margin-left: 3px;
    vertical-align: -2px;
    background: #A5B4FC;
    animation: cursorBlink 1s steps(1) infinite;
}
@keyframes typingBounce {
    0%, 60%, 100% { transform: translateY(0); opacity: 0.4; }
    30%            { transform: translateY(-6px); opacity: 1; }
}

/* Keyed native containers: no generated Emotion/BaseWeb class selectors.
   stLayoutWrapper is the container wrapper in the pinned Streamlit 1.65 UI. */
.st-key-documind_chat_panel {
    display: grid !important;
    /* Native flex containers use justify-content:start. With an implicit auto
       grid column that sizes to the empty-state content instead of the panel. */
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: auto minmax(0, 1fr) auto;
    justify-content: stretch !important;
    justify-items: stretch;
    gap: 12px !important;
    height: clamp(320px, calc(100vh - 160px), 880px) !important;
    height: clamp(320px, calc(100dvh - 160px), 880px) !important;
    min-width: 0;
}
/* Size the panel and its native rows independently of message content. */
.st-key-documind_chat_panel,
.st-key-documind_chat_panel > [data-testid="stLayoutWrapper"],
.st-key-documind_chat_header,
.st-key-documind_chat_viewport,
.st-key-documind_chat_composer {
    width: 100% !important;
    max-width: 100%;
    box-sizing: border-box;
}
.st-key-documind_chat_panel > [data-testid="stLayoutWrapper"] {
    min-height: 0;
    min-width: 0;
    height: auto !important;
}
.st-key-documind_chat_panel > [data-testid="stLayoutWrapper"]:has(> .st-key-documind_chat_viewport) {
    height: 100% !important;
    overflow: hidden;
}
.st-key-documind_chat_viewport {
    height: 100% !important;
    min-height: 0;
    overflow-y: auto !important;
    overflow-x: hidden;
    scrollbar-gutter: stable;
    overscroll-behavior-y: contain;
    padding: 4px 8px 12px 0;
}
.st-key-documind_chat_header,
.st-key-documind_chat_composer {
    background: #0E1117;
    min-width: 0;
}
.chat-panel-header {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 8px 16px;
    padding: 4px 0 10px;
    border-bottom: 1px solid #1E2535;
}
.chat-panel-title { color: #E2E8F0; font-size: 1.05rem; font-weight: 700; }
.chat-panel-subtitle { color: #8B9BB4; font-size: 0.82rem; }
.st-key-documind_chat_composer {
    position: sticky;
    bottom: 0;
    z-index: 2;
    padding: 8px 0 max(8px, env(safe-area-inset-bottom));
    border-top: 1px solid #1E2535;
}
/* The wrapper and key cover both the pinned UI and earlier native containers. */
.st-key-documind_sidebar_actions,
[data-testid="stSidebar"] [data-testid="stLayoutWrapper"]:has(> .st-key-documind_sidebar_actions) {
    position: sticky;
    top: 0;
    z-index: 10;
    background: #13151F;
    padding: 6px 0;
}
.st-key-documind_sidebar_actions button { min-height: 44px; }

/* Full-width cards with natural height for long questions, never clipped. */
.smart-question-card {
    display: flex;
    align-items: flex-start;
    gap: 16px;
    box-sizing: border-box;
    width: 100%;
    min-height: 128px;
    padding: 22px 24px;
    background: #141824;
    border: 1px solid #1E2535;
    border-left: 4px solid #6C63FF;
    border-radius: 10px;
}
.smart-question-number { flex: 0 0 auto; color: #A5B4FC; font-size: 1.1rem; font-weight: 800; }
.smart-question-text {
    min-width: 0;
    color: #CBD5E0;
    font-size: 1rem;
    line-height: 1.7;
    white-space: normal;
    overflow-wrap: anywhere;
}
.st-key-documind_smart_questions { gap: 24px !important; }
.st-key-documind_smart_questions button { min-height: 44px; }

/* ── Premium chat input styling ── */
[data-testid="stChatInput"] {
    border: 1px solid #2D3A5E !important;
    border-radius: 14px !important;
    background: #141824 !important;
    box-shadow: 0 0 0 2px #6C63FF22 !important;
}
[data-testid="stChatInput"]:focus-within {
    border-color: #6C63FF !important;
    box-shadow: 0 0 0 3px #6C63FF33 !important;
}
@keyframes cursorBlink { 50% { opacity: 0; } }
@media (prefers-reduced-motion: reduce) {
    .user-bubble,
    .assistant-bubble,
    .processing-dots span,
    .stream-cursor { animation: none !important; }
}
@media (max-width: 700px) {
    .st-key-documind_chat_panel {
        height: clamp(260px, calc(100vh - 140px), 620px) !important;
        height: clamp(260px, calc(100dvh - 140px), 620px) !important;
        gap: 8px !important;
    }
    .smart-question-card { min-height: 112px; padding: 18px 16px; gap: 12px; }
    .smart-question-text { font-size: 1rem; line-height: 1.65; }
    .user-bubble { max-width: 90%; }
    .assistant-bubble { max-width: 96%; padding: 10px 12px; }
    .assistant-bubble th,
    .assistant-bubble td { min-width: 92px; padding: 6px 7px; }
    .inline-citation-popover { width: min(300px, 72vw); }
}
</style>"""
