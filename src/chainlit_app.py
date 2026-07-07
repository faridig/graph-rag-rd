"""Chainlit chat interface — ACCRO R&D base de connaissances."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import os
import re
import threading
import time

import chainlit as cl
from chainlit.input_widget import TextInput
from chainlit.server import app as _fastapi_app

from src.cir import (
    GROUPEMENTS_VALIDES,
    CirResponse,
    build_cir_clients,
    export_docx,
    get_project_start_year,
    stream_fiche_cir,
)
from src.config import SSO_ENABLED
from src.generation.rag_pipeline import (
    QueryResponse,
    build_pipeline,
    extract_cited_ids,
    stream_query,
)
from src.models import Source
from src.query_log import log_query, record_feedback
from src.retrieval.literature import fetch_literature
from src.usage_tracker import check_budget, record_usage

_log = logging.getLogger(__name__)


# ── Cache-busting du CSS custom ───────────────────────────────────────────────
# Chainlit sert /public via un FileResponse nu (ETag + Last-Modified mais SANS
# Cache-Control, et SANS gestion du 304 conditionnel) → cache heuristique du
# navigateur. Après une modif de stylesheet.css, les utilisateurs gardaient
# l'ancienne charte jusqu'à un hard refresh (Ctrl+Shift+R).
# `no-cache` sur le SEUL .css force son re-téléchargement à chaque chargement
# (6 Ko — négligeable) → charte toujours à jour. On NE touche PAS aux polices
# (~450 Ko) ni aux images : sans support du 304, un no-cache global les
# re-téléchargerait entièrement à chaque page. theme.json n'est pas concerné
# (injecté inline dans le HTML, relu côté serveur à chaque chargement).
# Middleware enregistré à l'import du module, avant le démarrage d'uvicorn.
@_fastapi_app.middleware("http")
async def _revalidate_custom_css(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/public") and path.endswith(".css"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


# ── SSO Authentik (header-trust) ──────────────────────────────────────────────
# SSO_ENABLED=false en sandbox — l'infra nxtdeploy le passe à true en prod.
if SSO_ENABLED:

    @cl.header_auth_callback
    def header_auth_callback(headers: dict) -> cl.User | None:
        email = (headers.get("x-authentik-email") or "").lower().strip()
        if not email:
            return None
        name = (headers.get("x-authentik-name") or email.split("@")[0]).strip()
        return cl.User(
            identifier=email,
            display_name=name,
            metadata={"provider": "authentik"},
        )


_RUN_ID_RE = re.compile(r"\[source:\s*([^\]]+)\]")
_CIR_RE = re.compile(r"\bcir\b", re.IGNORECASE)
# Questions informatives → RAG, pas générateur
_CIR_QUESTION_RE = re.compile(
    r"\b(qu['\s]est[- ]ce|comment|pourquoi|c'est quoi|définition|"
    r"signifie|explication|à quoi sert|kesako)\b",
    re.IGNORECASE,
)

_pipeline = build_pipeline()
_cir_driver, _cir_anthropic = build_cir_clients()

_CIR_LABELS = ["Muscles HME", "Produits élaborés", "Nouvelles voies DST"]


# ── Helpers ───────────────────────────────────────────────────────────────────


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
        run_label = _format_run_id(s.run_id)
        if s.name:
            run_label += f" — {s.name}"
        if s.sharepoint_url:
            safe_url = html.escape(s.sharepoint_url, quote=True)
            title = (
                f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">'
                f"<strong>{run_label}</strong></a>"
            )
        else:
            title = f"<strong>{run_label}</strong>"
        suffix = (
            f" <em>(score : {s.score:.3f})</em>"
            if s.score > 0
            else " <em>(correspondance directe)</em>"
        )
        lines.append(f"<li>{title}{suffix}</li>")
    return "<ul>" + "".join(lines) + "</ul>"


def _history_from_session() -> list[dict]:
    """Build OpenAI-format history from Chainlit session messages (last 6)."""
    raw: list[dict] = cl.user_session.get("history") or []
    return raw[-6:]


# ── Starters (examples) ───────────────────────────────────────────────────────


@cl.set_starters
async def set_starters() -> list[cl.Starter]:
    return [
        cl.Starter(
            label="Huile et anisotropie M03",
            message="Quel effet a l'huile de tournesol sur l'anisotropie dans les essais M03 ?",
        ),
        cl.Starter(
            label="Psyllium Fibrinel PSL",
            message="Quelles expériences ont utilisé du psyllium Fibrinel PSL ?",
        ),
        cl.Starter(
            label="SME KOBE session 1",
            message="Comparer les valeurs de SME entre les runs KOBE session 1",
        ),
        cl.Starter(
            label="Anisotropie > 1,2",
            message="Quels essais ont atteint une anisotropie supérieure à 1,2 ?",
        ),
        cl.Starter(
            label="Générer une fiche CIR",
            message="CIR",
        ),
    ]


# ── CIR flow ──────────────────────────────────────────────────────────────────


async def _show_cir_groupement_picker() -> None:
    actions = [
        cl.Action(
            name="cir_groupement",
            value=GROUPEMENTS_VALIDES[i],
            label=_CIR_LABELS[i],
            payload={"groupement": GROUPEMENTS_VALIDES[i]},
        )
        for i in range(len(GROUPEMENTS_VALIDES))
    ]
    await cl.Message(
        content="Quel groupement voulez-vous documenter ?",
        actions=actions,
    ).send()


@cl.action_callback("cir_groupement")
async def on_cir_groupement(action: cl.Action) -> None:
    await action.remove()
    groupement = (action.payload or {}).get("groupement") or getattr(action, "value", None) or ""
    await _show_cir_year_picker(groupement)


async def _show_cir_year_picker(groupement: str) -> None:
    current_year = 2026
    years = [current_year - 1, current_year, current_year - 2]  # 2025, 2026, 2024
    actions = [
        cl.Action(
            name="cir_year",
            value=str(y),
            label=f"{y}" + (" (recommandé)" if y == current_year - 1 else ""),
            payload={"groupement": groupement, "year": y},
        )
        for y in years
    ]
    await cl.Message(
        content=f"Groupement : **{groupement}**\nQuelle année fiscale CIR ?",
        actions=actions,
    ).send()


@cl.action_callback("cir_year")
async def on_cir_year(action: cl.Action) -> None:
    await action.remove()
    payload = action.payload or {}
    groupement = payload.get("groupement") or ""
    cir_year = int(payload.get("year") or 0) or None
    await _run_cir_generation(groupement, cir_year=cir_year)


async def _run_cir_generation(groupement: str, cir_year: int | None = None) -> None:
    allowed, reason = check_budget()
    if not allowed:
        await cl.Message(content=f"**Limite atteinte.** {reason}").send()
        return

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    # Date de démarrage du projet (pour filtrer la littérature antérieure)
    start_year: int | None = None
    try:
        start_year = await asyncio.to_thread(get_project_start_year, _cir_driver, groupement)
    except Exception:
        _log.warning("get_project_start_year failed, proceeding without year filter", exc_info=True)

    # Pré-requête littérature (Semantic Scholar) avant de lancer le LLM
    literature_context: str | None = None
    async with cl.Step(name="Recherche littérature scientifique…", show_input=False) as lit_step:
        try:
            literature_context = await asyncio.to_thread(
                fetch_literature, groupement, 8, start_year
            )
            n = literature_context.count("\n- ") if literature_context else 0
            lit_step.output = (
                f"{n} article(s) trouvé(s)" if n else "Aucun article (API indisponible)"
            )
        except Exception:
            _log.warning("fetch_literature failed, proceeding without", exc_info=True)
            lit_step.output = "Indisponible — génération sans références externes"

    # Message principal — reçoit le stream LLM
    msg = cl.Message(content="")
    await msg.send()

    # Indicateur de chargement pendant la requête Neo4j + démarrage LLM
    async with cl.Step(name=f"Chargement des données — {groupement}", show_input=False):
        pass

    def _produce() -> None:
        try:
            for item in stream_fiche_cir(
                _cir_driver, _cir_anthropic, groupement, literature_context, cir_year
            ):
                asyncio.run_coroutine_threadsafe(queue.put(item), loop).result()
        except Exception:
            _log.exception("stream_fiche_cir failed")
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

    thread = threading.Thread(target=_produce, daemon=True)
    thread.start()

    final_response: CirResponse | None = None
    while True:
        item = await queue.get()
        if item is None:
            break
        if isinstance(item, str):
            await msg.stream_token(item)
        else:
            final_response = item

    await asyncio.to_thread(thread.join)

    if final_response is None:
        return

    await msg.update()

    if final_response.input_tokens or final_response.output_tokens:
        await asyncio.to_thread(
            record_usage, final_response.input_tokens, final_response.output_tokens
        )

    import tempfile as _tmp

    with _tmp.NamedTemporaryFile(suffix=".docx", delete=False) as f:
        tmp_path = f.name

    export_docx(final_response, tmp_path)

    # Enregistrer pour nettoyage en fin de session
    tmp_files: list[str] = cl.user_session.get("tmp_files") or []
    tmp_files.append(tmp_path)
    cl.user_session.set("tmp_files", tmp_files)

    safe_name = groupement.replace(" ", "_")[:40]
    elements = [
        cl.File(
            name=f"fiche_CIR_{safe_name}.docx",
            path=tmp_path,
            display="side",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    ]

    # Avertissement qualité + lien téléchargement dans un seul message
    warning_prefix = (
        f"⚠️ {final_response.data_quality.warning}\n\n"
        if final_response.data_quality.warning
        else ""
    )
    await cl.Message(
        content=f"{warning_prefix}✅ Fiche générée — cliquez pour télécharger :",
        elements=elements,
    ).send()


# ── Session init ──────────────────────────────────────────────────────────────


@cl.on_chat_start
async def on_chat_start() -> None:
    cl.user_session.set("history", [])
    cl.user_session.set("chantier", "")
    await cl.ChatSettings(
        [
            TextInput(
                id="chantier",
                label="Filtrer par chantier",
                placeholder="Ex : Extrusion HME",
                description="Laissez vide pour interroger tous les chantiers.",
            )
        ]
    ).send()
    await cl.Message(
        content=(
            "Bonjour ! Je suis la base de connaissances R&D ACCRO.\n\n"
            "Je couvre **170 expériences** (2 371 runs) issues des pôles "
            "Extrusion et Applications. "
            "Posez-moi des questions sur vos essais : effets d'ingrédients, valeurs mesurées, "
            "comparaisons entre runs, références croisées.\n\n"
            "⚠️ **La couverture des données est celle du Répertoire 2025-2026** — "
            "je ne dispose que des expériences et runs qui y sont enregistrés.\n\n"
            "**Quelques exemples :**\n"
            "- *Quel effet a l'huile de tournesol sur l'anisotropie dans les essais M03 ?*\n"
            "- *Quelles expériences ont utilisé du psyllium Fibrinel PSL ?*\n"
            "- *Comparer les valeurs de SME entre les runs KOBE session 1*\n\n"
            "Utilisez ⚙️ pour filtrer par chantier. "
            "Chaque réponse cite ses sources — cliquez pour ouvrir le fichier SharePoint."
        ),
        author="Assistant",
    ).send()


@cl.on_settings_update
async def on_settings_update(settings: dict) -> None:
    cl.user_session.set("chantier", settings.get("chantier", ""))


@cl.on_chat_end
async def on_chat_end() -> None:
    for path in cl.user_session.get("tmp_files") or []:
        with contextlib.suppress(OSError):
            os.unlink(path)


# ── Main message handler ──────────────────────────────────────────────────────


def _is_cir_generation_request(text: str) -> bool:
    """True si le message est une commande de génération CIR, pas une question informative."""
    if not _CIR_RE.search(text):
        return False
    return not _CIR_QUESTION_RE.search(text)


@cl.on_message
async def on_message(message: cl.Message) -> None:
    if _is_cir_generation_request(message.content):
        await _show_cir_groupement_picker()
        return

    allowed, reason = check_budget()
    if not allowed:
        await cl.Message(content=f"**Limite atteinte.** {reason}").send()
        return

    history = _history_from_session()
    chantier: str = cl.user_session.get("chantier") or ""

    _t0 = time.monotonic()
    msg = cl.Message(content="")
    await msg.send()

    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def _produce() -> None:
        try:
            for item in stream_query(
                _pipeline,
                message.content.strip(),
                chantier=chantier.strip() or None,
                history=history,
            ):
                asyncio.run_coroutine_threadsafe(queue.put(item), loop).result()
        except Exception:
            _log.exception("stream_query failed")
        finally:
            asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

    thread = threading.Thread(target=_produce, daemon=True)
    thread.start()

    accumulated = ""
    final_response: QueryResponse | None = None
    stream_done = False

    # Indicateur de réflexion TRANSITOIRE : spinner visible pendant le retrieval
    # + le démarrage du LLM (avant le 1er token), PUIS retiré dès l'arrivée de la
    # réponse. Sans le .remove(), le step terminé resterait affiché en permanence
    # sous la bulle (cot="full" garde les steps complétés → « Utilisé Recherche
    # dans le corpus… » persistant).
    async with cl.Step(name="Recherche dans le corpus…", show_input=False) as thinking:
        item = await queue.get()
        if item is None:
            stream_done = True
        elif isinstance(item, str):
            accumulated += item
            await msg.stream_token(_humanize_citations(item))
        else:
            final_response = item
    await thinking.remove()

    # Suite du flux (le premier item a déjà été consommé par le step ci-dessus)
    while not stream_done:
        item = await queue.get()
        if item is None:
            break
        if isinstance(item, str):
            accumulated += item
            await msg.stream_token(_humanize_citations(item))
        else:
            final_response = item

    await asyncio.to_thread(thread.join)

    if final_response is None:
        return

    answer = _humanize_citations(final_response.answer)

    # When the pipeline returns a response without streaming any tokens
    # (e.g. fallback found_in_corpus=False, triggered before generation),
    # the bubble is still empty — write the answer text explicitly so the
    # user sees the message instead of a blank bubble.
    if not accumulated and answer:
        await msg.stream_token(answer)

    cited_ids = extract_cited_ids(final_response.answer)
    if final_response.found_in_corpus and final_response.sources:
        display_sources = [
            s for s in final_response.sources if s.run_id in cited_ids
        ] or final_response.sources
        sources_md = _build_sources_md(display_sources)
        # Append sources as a stream token — msg.update() alone doesn't
        # re-render already-streamed content in Chainlit 2.x.
        await msg.stream_token(f"\n\n<hr/><strong>Sources</strong>\n{sources_md}")

    # ── Monitoring pertinence RAG (local, voir src/query_log.py) ──────────────
    # Journalise les signaux de la requête et branche les boutons 👍/👎 dessus.
    user = cl.user_session.get("user")
    query_id = log_query(
        question=message.content.strip(),
        found_in_corpus=final_response.found_in_corpus,
        dense_score=final_response.dense_score,
        fallback_reason=final_response.fallback_reason,
        n_chunks=final_response.n_chunks,
        n_sources=len(final_response.sources),
        n_cited=len(cited_ids),
        input_tokens=final_response.input_tokens,
        output_tokens=final_response.output_tokens,
        latency_ms=int((time.monotonic() - _t0) * 1000),
        chantier=chantier.strip() or None,
        user=getattr(user, "identifier", None),
    )
    msg.actions = [
        cl.Action(
            name="rag_feedback",
            label="👍 Pertinent",
            payload={"query_id": query_id, "value": 1},
        ),
        cl.Action(
            name="rag_feedback",
            label="👎 Peu utile",
            payload={"query_id": query_id, "value": 0},
        ),
    ]

    await msg.update()

    if final_response.input_tokens or final_response.output_tokens:
        await asyncio.to_thread(
            record_usage, final_response.input_tokens, final_response.output_tokens
        )

    # Save to session history (strip sources footer for context)
    history_entry_assistant = answer  # answer without sources markdown
    current_history: list[dict] = cl.user_session.get("history") or []
    current_history.append({"role": "user", "content": message.content.strip()})
    current_history.append({"role": "assistant", "content": history_entry_assistant})
    cl.user_session.set("history", current_history[-12:])  # keep last 6 exchanges


@cl.action_callback("rag_feedback")
async def on_rag_feedback(action: cl.Action) -> None:
    """Retour utilisateur 👍/👎 sur une réponse RAG → data/query_log.jsonl."""
    payload = action.payload or {}
    query_id = payload.get("query_id")
    value = int(payload.get("value", 0))
    if query_id:
        await asyncio.to_thread(record_feedback, query_id, value)
    await action.remove()
    with contextlib.suppress(Exception):
        await cl.context.emitter.send_toast("Merci pour votre retour !", type="success")
