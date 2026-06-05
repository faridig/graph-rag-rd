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


def _ask(question: str, chantier: str) -> tuple[str, str, str]:
    if not question.strip():
        return "", "", ""
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
            suffix = f" *(score: {s.score:.3f})*"
            if s.sharepoint_url:
                suffix += f" [📄 Ouvrir]({s.sharepoint_url})"
            lines.append(f"- {label}{suffix}")
        sources_md = "\n".join(lines)
    tokens_md = (
        f"**Tokens** — entrée : {response.input_tokens:,} · sortie : {response.output_tokens:,}"
        if response.found_in_corpus
        else ""
    )
    return _humanize_citations(response.answer), sources_md, tokens_md


with gr.Blocks(title="ACCRO R&D Knowledge Base") as demo:
    gr.Markdown("# ACCRO R&D — Base de connaissances")

    question = gr.Textbox(
        label="Question",
        placeholder="Ex : Quel effet a l'huile sur M03 ?",
        lines=2,
    )

    submit = gr.Button("Rechercher", variant="primary")
    status = gr.Markdown(value="", visible=False)

    answer = gr.Markdown(label="Réponse")
    sources = gr.Markdown(label="Sources")
    tokens = gr.Markdown(label="")

    def _set_loading() -> tuple:
        return gr.update(interactive=False, value="Recherche en cours…"), gr.update(visible=True, value="_Recherche en cours…_"), "", "", ""

    def _ask_and_reset(question: str) -> tuple:
        answer_text, sources_text, tokens_text = _ask(question, "")
        return gr.update(interactive=True, value="Rechercher"), gr.update(visible=False, value=""), answer_text, sources_text, tokens_text

    for event in (submit.click, question.submit):
        event(
            fn=_set_loading,
            inputs=[],
            outputs=[submit, status, answer, sources, tokens],
            queue=False,
        ).then(
            fn=_ask_and_reset,
            inputs=[question],
            outputs=[submit, status, answer, sources, tokens],
        )


if __name__ == "__main__":
    demo.launch(theme=gr.themes.Soft())
