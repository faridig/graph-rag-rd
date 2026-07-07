"""Gradio chat interface — ACCRO R&D brand design."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator

import gradio as gr

from src.generation.rag_pipeline import (
    QueryResponse,
    build_pipeline,
    extract_cited_ids,
    stream_query,
)
from src.models import Source

_log = logging.getLogger(__name__)
_pipeline = build_pipeline()
_RUN_ID_RE = re.compile(r"\[source:\s*([^\]]+)\]")

_STATIC = "src/static"

# ── ACCRO theme ───────────────────────────────────────────────────────────────
_PINK = gr.themes.Color(
    c50="#fdeaf3",
    c100="#fbd5e8",
    c200="#f7aad1",
    c300="#f37fba",
    c400="#ef55a3",
    c500="#e72f7f",
    c600="#c4196a",
    c700="#a8175a",
    c800="#8b1249",
    c900="#6e0d38",
    c950="#510a29",
)
_GREEN = gr.themes.Color(
    c50="#eef6e9",
    c100="#d4ecca",
    c200="#a9d895",
    c300="#7fc460",
    c400="#65b442",
    c500="#499b2d",
    c600="#3a7e23",
    c700="#2c6119",
    c800="#1e4410",
    c900="#102808",
    c950="#080f04",
)
_INK = gr.themes.Color(
    c50="#f5f4ee",
    c100="#ebebe4",
    c200="#d6d6cf",
    c300="#a9aaa3",
    c400="#8a8b85",
    c500="#6b6c66",
    c600="#55564f",
    c700="#3a3b37",
    c800="#2a2b27",
    c900="#1b1c18",
    c950="#151614",
)

_accro_theme = gr.themes.Base(
    primary_hue=_PINK,
    secondary_hue=_GREEN,
    neutral_hue=_INK,
    font=[gr.themes.GoogleFont("Montserrat"), "sans-serif"],
).set(
    body_background_fill="#fbf6ec",
    body_background_fill_dark="#fbf6ec",
    background_fill_primary="#fbf6ec",
    background_fill_secondary="#ffffff",
    border_color_primary="#151614",
    border_color_accent="#e72f7f",
    button_primary_background_fill="#e72f7f",
    button_primary_background_fill_hover="#c4196a",
    button_primary_text_color="#ffffff",
    button_primary_border_color="#151614",
    button_secondary_background_fill="#ffffff",
    input_background_fill="#ffffff",
    block_background_fill="#fbf6ec",
    panel_background_fill="#fbf6ec",
)

# ── CSS ───────────────────────────────────────────────────────────────────────
# Selectors verified against Gradio 6.15.2 source (Index-B80bIer5.css):
#   .user  → user message content div  (background-color: var(--color-accent-soft))
#   .bot   → bot message content div   (background-color: var(--background-fill-secondary))
#   .bubble.user-row / .bubble.bot-row → outer margin/alignment wrappers
_CSS = f"""
/* ── Fonts ── */
@font-face {{
  font-family: "Londrina Solid";
  src: url("/file={_STATIC}/fonts/LondrinaSolid-Black.ttf") format("truetype");
  font-weight: 900;
}}
@font-face {{
  font-family: "Londrina Solid";
  src: url("/file={_STATIC}/fonts/LondrinaSolid-Regular.ttf") format("truetype");
  font-weight: 400;
}}
@font-face {{
  font-family: "Cocogoose Pro";
  src: url("/file={_STATIC}/fonts/Cocogoose-Pro-Light.ttf") format("truetype");
  font-weight: 400;
}}

/* ── Global ── */
body, .gradio-container {{
  font-family: "Cocogoose Pro", "Montserrat", sans-serif !important;
  background: #fbf6ec !important;
}}
.gradio-container {{
  max-width: 900px !important;
  margin: 0 auto !important;
}}

/* ── ACCRO custom header (injected via JS) ── */
.accro-header {{
  background: #151614;
  padding: 14px 22px 12px;
  display: flex;
  align-items: center;
  gap: 14px;
  border-bottom: 5px solid #e72f7f;
}}
.accro-hdr-logo {{
  width: 50px; height: 50px; object-fit: contain; flex-shrink: 0;
}}
.accro-hdr-body {{ flex: 1; min-width: 0; }}
.accro-hdr-title {{
  font-family: "Londrina Solid", "Anton", sans-serif;
  font-weight: 900;
  font-size: 1.5rem;
  color: #feca21;
  letter-spacing: 0.02em;
  line-height: 1;
  text-transform: uppercase;
}}
.accro-hdr-title em {{ color: #ffffff; font-style: normal; }}
.accro-hdr-desc {{ font-size: 0.78rem; color: #a9aaa3; margin-top: 3px; }}
.accro-hdr-badge {{
  background: #499b2d;
  color: #ffffff;
  font-family: "Londrina Solid", sans-serif;
  font-size: 0.72rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 4px 12px;
  border-radius: 999px;
  border: 2px solid #ffffff;
  flex-shrink: 0;
  white-space: nowrap;
}}

/* ── Hide Gradio built-in title/description block ── */
.prose.md:first-child, .app > h1, h1.gradio-header {{
  display: none !important;
}}

/* ── Chatbot background ── */
.chatbot, .bubble-wrap {{
  background: #fbf6ec !important;
}}

/* ── USER bubble: pink, ink border, asymmetric radius ──
   Selector matches: .user (Gradio 6.x chatbot content div)              */
.message-wrap .user {{
  background-color: #e72f7f !important;
  color: #ffffff !important;
  border: 3px solid #151614 !important;
  border-radius: 22px 22px 6px 22px !important;
  box-shadow: 4px 4px 0 0 #151614 !important;
  padding: 11px 15px !important;
}}
.message-wrap .user p,
.message-wrap .user li,
.message-wrap .user a {{
  color: #ffffff !important;
}}

/* ── BOT bubble: white, ink border, asymmetric radius ── */
.message-wrap .bot {{
  background-color: #ffffff !important;
  color: #3a3b37 !important;
  border: 3px solid #151614 !important;
  border-radius: 6px 22px 22px 22px !important;
  box-shadow: 4px 4px 0 0 #151614 !important;
  padding: 11px 15px !important;
}}
.message-wrap .bot strong {{ color: #151614; }}
.message-wrap .bot a {{ color: #499b2d; }}

/* ── Input textarea ── */
label.block textarea, .block textarea {{
  background: #ffffff !important;
  border: 3px solid #151614 !important;
  border-radius: 14px !important;
  box-shadow: 3px 3px 0 0 #d6d6cf !important;
  font-family: "Cocogoose Pro", "Montserrat", sans-serif !important;
  font-size: 0.9375rem !important;
  color: #3a3b37 !important;
  transition: box-shadow 150ms !important;
}}
label.block textarea:focus, .block textarea:focus {{
  box-shadow: 4px 4px 0 0 #e72f7f !important;
  outline: none !important;
  border-color: #151614 !important;
}}

/* ── Primary (submit) button ── */
button.primary {{
  background: #e72f7f !important;
  color: #ffffff !important;
  border: 3px solid #151614 !important;
  border-radius: 14px !important;
  box-shadow: 4px 4px 0 0 #151614 !important;
  font-family: "Londrina Solid", "Anton", sans-serif !important;
  font-size: 1rem !important;
  font-weight: 400 !important;
  letter-spacing: 0.06em !important;
  text-transform: uppercase !important;
  transition: transform 120ms, box-shadow 120ms !important;
}}
button.primary:hover:not([disabled]) {{
  transform: translate(-2px, -2px) !important;
  box-shadow: 7px 7px 0 0 #151614 !important;
  background: #c4196a !important;
}}
button.primary:active:not([disabled]) {{
  transform: translate(2px, 2px) !important;
  box-shadow: none !important;
}}
button.primary[disabled] {{
  background: #a9aaa3 !important;
  box-shadow: 2px 2px 0 0 #6b6c66 !important;
}}

/* ── Examples ── */
.example {{
  background: #ffffff !important;
  border: 3px solid #151614 !important;
  border-radius: 14px !important;
  box-shadow: 4px 4px 0 0 #151614 !important;
  font-family: "Cocogoose Pro", "Montserrat", sans-serif !important;
  font-size: 0.8rem !important;
  color: #3a3b37 !important;
  transition: transform 150ms, box-shadow 150ms !important;
}}
.example:hover {{
  transform: translate(-2px, -2px) !important;
  box-shadow: 7px 7px 0 0 #151614 !important;
  background: #fff0f8 !important;
}}
.example:active {{
  transform: translate(2px, 2px) !important;
  box-shadow: none !important;
}}

/* ── Accordion options ── */
.accordion .label-wrap {{
  font-family: "Cocogoose Pro", "Montserrat", sans-serif !important;
  color: #6b6c66 !important;
  font-size: 0.8rem !important;
}}

/* ── Scrollbar ── */
::-webkit-scrollbar {{ width: 5px; }}
::-webkit-scrollbar-thumb {{ background: #d6d6cf; border-radius: 3px; }}
"""

# ── JS: inject ACCRO header after page load ───────────────────────────────────
_INJECT_HEADER = f"""
() => {{
  if (document.querySelector('.accro-header')) return;
  const logo = '/file={_STATIC}/stickers/sticker-accro-heart.png';
  const hdr = document.createElement('header');
  hdr.className = 'accro-header';
  hdr.innerHTML =
    '<img class="accro-hdr-logo" src="' + logo + '" alt="ACCRO" />' +
    '<div class="accro-hdr-body">' +
      '<div class="accro-hdr-title">ACCRO <em>R&D</em> — <em>Base de connaissances</em></div>' +
      '<div class="accro-hdr-desc">Répertoire R&D · Extrusion · ' +
      'Formulations · Résultats mesurés</div>' +
    '</div>' +
    '<span class="accro-hdr-badge">100 % végétal</span>';
  const container = document.querySelector('.gradio-container') || document.body;
  container.insertBefore(hdr, container.firstChild);
}}
"""

# ── Helpers ───────────────────────────────────────────────────────────────────
_EXAMPLES = [
    ["Quel effet a l'huile de tournesol sur l'anisotropie dans les essais M03 ?", ""],
    ["Quelles expériences ont utilisé du psyllium Fibrinel PSL ?", ""],
    ["Quels sont les résultats de l'essai STRIP-B09 ?", ""],
    ["Comparer les valeurs de SME entre les runs KOBE session 1", ""],
    ["Quels essais ont atteint une anisotropie supérieure à 1.2 ?", ""],
]


def _format_run_id(run_id: str) -> str:
    parts = run_id.split(":Run:", 1)
    if len(parts) == 2:
        exp = "RÉPERTOIRE" if parts[0].upper().startswith("REPERTOIRE") else parts[0]
        local = parts[1]
        try:
            return f"{exp} — essai {int(local)}"
        except ValueError:
            return f"{exp} — {local}"
    return run_id


def _humanize_citations(text: str) -> str:
    return _RUN_ID_RE.sub(lambda m: f"[source: {_format_run_id(m.group(1).strip())}]", text)


def _build_sources_md(sources: list[Source]) -> str:
    seen: set[str] = set()
    lines = []
    for s in sources:
        if s.run_id in seen:
            continue
        seen.add(s.run_id)
        label = f"**{_format_run_id(s.run_id)}**"
        if s.name:
            label += f" — {s.name}"
        suffix = f" *(score : {s.score:.3f})*" if s.score > 0 else " *(correspondance directe)*"
        if s.sharepoint_url:
            suffix += f" [📄 Ouvrir]({s.sharepoint_url})"
        lines.append(f"- {label}{suffix}")
    return "\n".join(lines)


# ── Chat function ─────────────────────────────────────────────────────────────
def chat_fn(
    message: str,
    history: list,
    chantier: str,
) -> Iterator[str]:
    accumulated = ""
    final_response: QueryResponse | None = None

    try:
        for item in stream_query(
            _pipeline,
            message.strip(),
            chantier=chantier.strip() or None,
            history=history or [],
        ):
            if isinstance(item, str):
                accumulated += item
                yield _humanize_citations(accumulated)
            else:
                final_response = item
    except Exception:
        _log.exception("stream_query failed")
        yield (
            "Une erreur est survenue lors de la recherche. "
            "Vérifiez que Neo4j est démarré (`docker compose up -d`)."
        )
        return

    if final_response is None:
        return

    answer = _humanize_citations(final_response.answer)

    if final_response.found_in_corpus and final_response.sources:
        cited_ids = extract_cited_ids(final_response.answer)
        display_sources = [
            s for s in final_response.sources if s.run_id in cited_ids
        ] or final_response.sources
        sources_md = _build_sources_md(display_sources)
        yield f"{answer}\n\n---\n**Sources**\n{sources_md}"
    else:
        yield answer


# ── Interface ─────────────────────────────────────────────────────────────────
_chantier_input = gr.Textbox(
    label="Filtrer par chantier (optionnel)",
    placeholder="Ex : Extrusion HME",
    lines=1,
    render=False,
)

demo = gr.ChatInterface(
    fn=chat_fn,
    chatbot=gr.Chatbot(
        height=520,
        render_markdown=True,
        layout="bubble",
        buttons=["copy"],
    ),
    textbox=gr.Textbox(
        placeholder="Ex : Quel effet a l'huile sur M03 ?",
        container=False,
        scale=7,
    ),
    title=None,
    description=None,
    examples=_EXAMPLES,
    additional_inputs=[_chantier_input],
    additional_inputs_accordion=gr.Accordion("Options", open=False),
    save_history=False,
    concurrency_limit=4,
    flagging_mode="manual",
    flagging_options=["Réponse incorrecte", "Source manquante", "Hors sujet"],
)

with demo:
    demo.load(fn=None, js=_INJECT_HEADER)

if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        allowed_paths=[_STATIC],
        theme=_accro_theme,
        css=_CSS,
    )
