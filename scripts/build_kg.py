#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn a canonical knowledge JSON (see references/schema.md) into KG-ready artifacts.

Usage:
    python build_kg.py <id>_knowledge.json --outdir OUT

Writes into OUT:
    <id>_triples.csv        subject,predicate,object,unit   (load this into your graph)
    <id>_documentation.md   readable synthesis (identity, design, inputs, conditions, results)
    <id>_validation.md      consistency checks (replicate vs mean, missing units, empty fields)

The walker is generic: it relies only on the schema conventions, so one emitter serves any
experiment. It never invents data — it only re-expresses what the JSON contains, plus
recomputed sanity checks flagged as such.
"""
import sys, os, json, csv, argparse, math, re

MEAS_KEYS = ("value", "mean", "sd", "cv", "setpoint", "actual", "range", "replicates", "unit", "note")

def is_meas(d):
    return isinstance(d, dict) and any(k in d for k in
        ("value", "mean", "actual", "setpoint", "range", "replicates"))

def safe(s):
    return re.sub(r"\s+", "_", str(s).strip())

def _cell(v) -> str:
    """Render a markdown table cell — None becomes empty string."""
    return "" if v is None else str(v)

# --------------------------------------------------------------------------- triples
class Triples:
    def __init__(self): self.rows = []
    def add(self, s, p, o, u=""):
        if o is None or o == "": return
        self.rows.append([s, p, str(o), u])

def emit_measurement(T, node, metric, m):
    u = m.get("unit", "") or ""
    for key, suf in [("value", ""), ("mean", "_mean"), ("sd", "_sd"),
                     ("setpoint", "_setpoint"), ("actual", "_actual"), ("range", "_range")]:
        if m.get(key) is not None:
            T.add(node, metric + suf, m[key], u)
    if m.get("cv") is not None:
        T.add(node, metric + "_cv", m["cv"], "%")
    reps = m.get("replicates") or []
    if not isinstance(reps, list):
        reps = []
    for i, rep in enumerate(reps, 1):
        if isinstance(rep, dict):
            for rk, rv in rep.items():
                T.add(node, f"{metric}_rep{i}_{safe(rk)}", rv)
        else:
            T.add(node, f"{metric}_rep{i}", rep, u)
    if m.get("note"):
        T.add(node, metric + "_note", m["note"])

def emit_scalarmap(T, node, prefix, d):
    """Emit a map whose leaves are either measurements or plain scalars."""
    for k, v in d.items():
        metric = safe(k)
        if is_meas(v):
            emit_measurement(T, node, metric, v)
        elif isinstance(v, dict):
            emit_scalarmap(T, node, metric, v)   # nested map -> flatten with prefixed metric
        elif isinstance(v, list):
            continue  # lists at this level are handled elsewhere (inputs)
        else:
            T.add(node, (prefix + "_" if prefix else "") + metric, v)

def build_triples(doc):
    T = Triples()
    exp = doc["experiment"]; eid = safe(exp["id"])
    T.add(eid, "rdf:type", "Experiment")
    for k, v in exp.items():
        if k != "id" and not isinstance(v, (dict, list)):
            T.add(eid, safe(k), v)

    # design
    factor_units = {}
    for fdef in doc.get("design", {}).get("factors", []):
        factor_units[fdef["name"]] = fdef.get("unit") or ""
        T.add(eid, "hasFactor", fdef["name"])
        for lvl in (fdef.get("levels") or []):
            T.add(fdef["name"], "hasLevel", lvl, fdef.get("unit") or "")
    if doc.get("design", {}).get("control") is not None:
        T.add(eid, "controlRun", f"{eid}:Run:{safe(doc['design']['control'])}")

    # runs
    for run in doc.get("runs", []):
        rid = safe(run["id"]); node = f"{eid}:Run:{rid}"
        T.add(node, "rdf:type", "Run"); T.add(eid, "hasRun", node)
        if run.get("name"): T.add(node, "name", run["name"])
        if run.get("is_control"): T.add(node, "isControl", "true")
        for f, lvl in (run.get("factor_levels") or {}).items():
            T.add(node, "hasFactor_" + safe(f), lvl, factor_units.get(f, ""))
        # inputs (named sub-tables of component lists)
        for table, rows in (run.get("inputs") or {}).items():
            if not isinstance(rows, list): continue
            for comp in rows:
                cname = comp.get("component", "item")
                cnode = f"{node}:in:{safe(table)}:{safe(cname)}"
                T.add(node, "hasInput", cnode)
                T.add(cnode, "rdf:type", "Input"); T.add(cnode, "inputTable", table)
                T.add(cnode, "component", cname)
                for k, v in comp.items():
                    if k == "component": continue
                    if is_meas(v): emit_measurement(T, cnode, safe(k), v)
                    elif not isinstance(v, (dict, list)): T.add(cnode, safe(k), v)
        # conditions & responses (maps of measurements)
        for sect in ("conditions", "responses"):
            for metric, m in (run.get(sect) or {}).items():
                if is_meas(m): emit_measurement(T, node, safe(metric), m)
                elif isinstance(m, dict): emit_scalarmap(T, node, safe(metric), m)
                elif not isinstance(m, list): T.add(node, safe(metric), m)
        if run.get("notes"): T.add(node, "note", run["notes"])

    # derived (clearly namespaced)
    for d in doc.get("derived", []):
        node = f"{eid}:Run:{safe(d['run'])}"
        for k, v in (d.get("vs_control_pct") or {}).items():
            T.add(node, "derived_deltaVsControl_" + safe(k), v, "%")

    # calibrations (instrument/pump calibration curves)
    for cal in doc.get("calibrations", []):
        cnode = f"{eid}:Calibration:{safe(cal.get('name','cal'))}"
        T.add(eid, "hasCalibration", cnode); T.add(cnode, "rdf:type", "Calibration")
        for k, v in cal.items():
            if k != "points" and not isinstance(v, (list, dict)):
                T.add(cnode, safe(k), v)
        for i, pt in enumerate(cal.get("points", []), 1):
            pnode = f"{cnode}:pt{i}"
            T.add(cnode, "hasPoint", pnode); T.add(pnode, "rdf:type", "CalibrationPoint")
            for k, v in pt.items():
                if not isinstance(v, (list, dict)):
                    T.add(pnode, safe(k), v)

    # observations / lists
    for k, v in (doc.get("observations") or {}).items():
        T.add(eid, "observation_" + safe(k), v)
    for item in doc.get("not_measured", []): T.add(eid, "notMeasured", item)
    for item in doc.get("unused_palette", []): T.add(eid, "unusedPalette", item)
    return T

# --------------------------------------------------------------------------- markdown
def md_measure(m):
    if not is_meas(m):
        return str(m)
    u = m.get("unit") or ""
    if m.get("mean") is not None:
        s = f"{m['mean']} ± {m.get('sd','?')} {u}".strip()
        if m.get("cv") is not None: s += f" (CV {m['cv']}%)"
        return s
    if m.get("value") is not None: return f"{m['value']} {u}".strip()
    parts = []
    if m.get("setpoint") is not None: parts.append(f"consigne {m['setpoint']}")
    if m.get("actual") is not None: parts.append(f"réel {m['actual']}")
    if m.get("range") is not None: parts.append(f"plage {m['range']}")
    return (", ".join(parts) + (f" {u}" if u else "")).strip() or "—"

def build_markdown(doc):
    exp = doc["experiment"]; L = []
    L.append(f"# {exp.get('id','')} — {exp.get('title','')}\n")
    L.append(f"> Exhaustive structured synthesis derived from `{exp.get('source_file','')}` "
             f"for knowledge-graph ingestion. Values extracted by script; the *variations vs "
             f"control* block is computed.\n")
    L.append("## 1. Identity")
    for k in ("id","type","objective","date","operator","equipment","domain"):
        if exp.get(k): L.append(f"- **{k}**: {exp[k]}")
    # design
    if doc.get("design"):
        L.append("\n## 2. Design of experiment")
        for f in doc["design"].get("factors", []):
            lv = ", ".join(str(x) for x in f.get("levels", []))
            L.append(f"- **{f['name']}**{(' ('+f['unit']+')') if f.get('unit') else ''}: {lv}")
        if doc["design"].get("control") is not None:
            L.append(f"- **control run**: {doc['design']['control']}")
    # runs
    L.append("\n## 3. Runs")
    for run in doc.get("runs", []):
        L.append(f"\n### Run {run['id']} — {run.get('name','')}"
                 + ("  *(control)*" if run.get("is_control") else ""))
        if run.get("factor_levels"):
            L.append("- factors: " + ", ".join(f"{k}={v}" for k, v in run["factor_levels"].items()))
        for table, rows in (run.get("inputs") or {}).items():
            if not isinstance(rows, list) or not rows: continue
            L.append(f"\n**Inputs — {table}**\n")
            keys = [k for k in rows[0].keys() if k != "component"]
            L.append("| component | " + " | ".join(keys) + " |")
            L.append("|" + "---|" * (len(keys) + 1))
            for c in rows:
                L.append("| " + _cell(c.get("component")) + " | "
                         + " | ".join(md_measure(c.get(k)) if is_meas(c.get(k)) else _cell(c.get(k))
                                      for k in keys) + " |")
        if run.get("conditions"):
            L.append("\n**Conditions**\n")
            for k, v in run["conditions"].items(): L.append(f"- {k}: {md_measure(v)}")
        if run.get("responses"):
            L.append("\n**Responses**\n")
            for k, v in run["responses"].items(): L.append(f"- {k}: {md_measure(v)}")
        if run.get("notes"): L.append(f"\n_Note: {run['notes']}_")
    # derived — two modes: vs_control_pct (table) or computed (bullet list)
    if doc.get("derived"):
        L.append("\n## 4. Derived & computed values")
        # Only numeric keys qualify for the % table — string values (e.g. notes) go to bullets.
        def _fmt_pct(v):
            if v is None: return "—"
            if isinstance(v, (int, float)): return f"{v:+}%"
            return str(v)
        allk = sorted({k for d in doc["derived"]
                       for k, v in (d.get("vs_control_pct") or {}).items()
                       if isinstance(v, (int, float, type(None)))})
        if allk:
            L.append("| run | " + " | ".join(allk) + " |")
            L.append("|" + "---|" * (len(allk) + 1))
            for d in doc["derived"]:
                row = d.get("vs_control_pct") or {}
                L.append(f"| {d.get('label', d['run'])} | "
                         + " | ".join(_fmt_pct(row.get(k)) for k in allk) + " |")
        for d in doc["derived"]:
            if d.get("computed"):
                L.append(f"\n**{d.get('label', d['run'])}**")
                if d.get("note"):
                    L.append(f"_{d['note']}_")
                for ck, cv in d["computed"].items():
                    L.append(f"- {ck}: {cv}")
    # observations
    if doc.get("observations"):
        L.append("\n## 5. Observations & conclusions")
        for k, v in doc["observations"].items(): L.append(f"- **{k}**: {v}")
    if doc.get("not_measured"):
        # Support both legacy strings and enriched objects {analysis, reason}
        items = []
        for item in doc["not_measured"]:
            if isinstance(item, dict):
                label = item.get("analysis", str(item))
                reason = item.get("reason")
                items.append(f"{label} ({reason})" if reason else label)
            else:
                items.append(str(item))
        L.append("\n## 6. Planned but not measured\n" + ", ".join(items) + ".")
    if doc.get("unused_palette"):
        L.append("\n## 7. Unused material palette\n" + ", ".join(doc["unused_palette"]) + ".")
    if doc.get("calibrations"):
        L.append("\n## 7b. Calibrations")
        for cal in doc["calibrations"]:
            L.append(f"\n**{cal.get('name','calibration')}** — {cal.get('note','')}")
            pts = cal.get("points", [])
            if pts:
                keys = list(pts[0].keys())
                L.append("| " + " | ".join(keys) + " |")
                L.append("|" + "---|" * len(keys))
                for pt in pts:
                    L.append("| " + " | ".join(_cell(pt.get(k)) for k in keys) + " |")
    if doc.get("glossary"):
        L.append("\n## 8. Glossary")
        for k, v in doc["glossary"].items(): L.append(f"- **{k}**: {v}")
    return "\n".join(L)

# --------------------------------------------------------------------------- validation
def validate(doc):
    issues = []; ok = []
    n_runs = len(doc.get("runs", []))
    n_meas = 0; n_missing_unit = 0
    for run in doc.get("runs", []):
        for sect in ("conditions", "responses"):
            for metric, m in (run.get(sect) or {}).items():
                if not is_meas(m): continue
                n_meas += 1
                # missing unit (ratios/indices legitimately null -> only warn if key absent)
                if "unit" not in m:
                    n_missing_unit += 1
                    issues.append(f"Run {run['id']} · {metric}: no `unit` key (use null if dimensionless).")
                # replicate vs mean
                raw_reps = m.get("replicates") or []
                raw_reps = raw_reps if isinstance(raw_reps, list) else []
                reps = [r for r in raw_reps if isinstance(r, (int, float))]
                if reps and isinstance(m.get("mean"), (int, float)):
                    calc = sum(reps) / len(reps)
                    if m["mean"] != 0 and abs(calc - m["mean"]) / abs(m["mean"]) > 0.02:
                        issues.append(f"Run {run['id']} · {metric}: mean {m['mean']} vs recomputed "
                                      f"{round(calc,3)} from {len(reps)} replicates (>2% off).")
                    else:
                        ok.append(f"Run {run['id']} · {metric}: mean consistent with replicates.")
                # cv vs sd/mean
                if all(isinstance(m.get(x), (int, float)) for x in ("cv", "sd", "mean")) and m["mean"]:
                    calc = m["sd"] / m["mean"] * 100
                    if abs(calc - m["cv"]) > max(0.5, 0.05 * abs(m["cv"])):
                        issues.append(f"Run {run['id']} · {metric}: CV {m['cv']} vs recomputed {round(calc,2)}.")
    L = ["# Validation report\n",
         f"- runs: {n_runs}",
         f"- measurements: {n_meas}",
         f"- measurements missing a `unit` key: {n_missing_unit}",
         f"- consistency checks passed: {len(ok)}",
         f"- issues flagged: {len(issues)}\n"]
    if issues:
        L.append("## Issues to fix (edit the extraction script, then re-run)")
        L += [f"- {i}" for i in issues]
    else:
        L.append("✅ No consistency issues detected.")
    # required fields
    for req in ("experiment", "runs"):
        if not doc.get(req): L.append(f"\n⚠ Missing required top-level key: `{req}`.")
    if not doc.get("glossary"): L.append("\n⚠ No glossary — add definitions for domain terms.")
    if not doc.get("provenance"): L.append("\n⚠ No provenance block — record extraction method.")
    return "\n".join(L)

# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--outdir", default=".")
    a = ap.parse_args()
    doc = json.load(open(a.json_path, encoding="utf-8"))
    eid = safe(doc["experiment"]["id"])
    os.makedirs(a.outdir, exist_ok=True)

    T = build_triples(doc)
    tcsv = os.path.join(a.outdir, f"{eid}_triples.csv")
    with open(tcsv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["subject", "predicate", "object", "unit"]); w.writerows(T.rows)

    mdoc = os.path.join(a.outdir, f"{eid}_documentation.md")
    open(mdoc, "w", encoding="utf-8").write(build_markdown(doc))

    vdoc = os.path.join(a.outdir, f"{eid}_validation.md")
    open(vdoc, "w", encoding="utf-8").write(validate(doc))

    print(f"triples : {tcsv}  ({len(T.rows)} rows)")
    print(f"markdown: {mdoc}")
    print(f"validate: {vdoc}")
    print("\n--- validation summary ---")
    print(validate(doc).split("## ")[0])

if __name__ == "__main__":
    main()
