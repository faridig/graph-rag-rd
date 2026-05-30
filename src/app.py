"""Gradio interface for ACCRO Graph RAG."""

from __future__ import annotations

import gradio as gr

import re

from src.generation.rag_pipeline import build_pipeline, run_query

_pipeline = build_pipeline()

_RUN_ID_RE = re.compile(r"\[source:\s*([^\]]+)\]")


def _format_run_id(run_id: str) -> str:
    """Allumette:Run:1 → Allumette — essai 1 ; REPERTOIRE-RD-2025-2026:Run:M03 → RÉPERTOIRE — M03."""
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


def _ask(question: str, chantier: str) -> tuple[str, str]:
    if not question.strip():
        return "", ""
    response = run_query(
        _pipeline,
        question.strip(),
        chantier=chantier.strip() or None,
    )
    sources_md = ""
    if response.found_in_corpus and response.sources:
        lines = []
        for s in response.sources:
            label = f"**{_format_run_id(s.run_id)}**"
            if s.name:
                label += f" — {s.name}"
            lines.append(f"- {label} *(score: {s.score:.3f})*")
        sources_md = "\n".join(lines)
    return _humanize_citations(response.answer), sources_md


with gr.Blocks(title="ACCRO R&D Knowledge Base") as demo:
    gr.Markdown(
        "# ACCRO R&D — Base de connaissances\n"
        "Interrogez les essais R&D (RÉPERTOIRE 2025-2026, ACE-3, ACE-5)."
    )

    with gr.Row():
        with gr.Column(scale=4):
            question = gr.Textbox(
                label="Question",
                placeholder="Ex : Quel effet a l'huile sur M03 ?",
                lines=2,
            )
        with gr.Column(scale=1):
            chantier = gr.Textbox(
                label="Filtrer par chantier (optionnel)",
                placeholder="Ex : Extrusion",
            )

    submit = gr.Button("Rechercher", variant="primary")
    status = gr.Markdown(value="", visible=False)

    answer = gr.Markdown(label="Réponse")
    sources = gr.Markdown(label="Sources")

    def _set_loading() -> tuple:
        return gr.update(interactive=False, value="Recherche en cours…"), gr.update(visible=True, value="_Recherche en cours…_"), "", ""

    def _ask_and_reset(question: str, chantier: str) -> tuple:
        answer_text, sources_text = _ask(question, chantier)
        return gr.update(interactive=True, value="Rechercher"), gr.update(visible=False, value=""), answer_text, sources_text

    for event in (submit.click, question.submit):
        event(
            fn=_set_loading,
            inputs=[],
            outputs=[submit, status, answer, sources],
            queue=False,
        ).then(
            fn=_ask_and_reset,
            inputs=[question, chantier],
            outputs=[submit, status, answer, sources],
        )

    gr.Examples(
        examples=[
            ["Quel effet a l'huile sur la texture de M03 ?", ""],
            ["Synthèse des essais fibres en extrusion", ""],
            ["Kobé arôme boeuf TVP résultats", ""],
            ["Pisane ES a-t-il été testé ?", ""],
        ],
        inputs=[question, chantier],
    )

if __name__ == "__main__":
    demo.launch(theme=gr.themes.Soft())
