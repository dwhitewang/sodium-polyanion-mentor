#!/usr/bin/env python3
"""Offline, standard-library retrieval and preflight. No scientific acceptance."""
import argparse
import html
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8-sig"))


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def normalize_doi(value):
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value.strip(), flags=re.I).lower().rstrip(".,; ")


def source(doi):
    doi = normalize_doi(doi)
    record = next((r for r in load("corpus.json")["records"] if r["doi"] == doi), None)
    if record is None:
        return {"doi": doi, "status": "not_in_159_identity_registry", "note": "Not a verdict on existence or quality; verify separately if needed."}
    if record["quarantine"]:
        return {"id": record["id"], "doi": doi, "quarantine": True, "usable_for_recommendation": False, "note": "Metadata only; do not migrate scientific content."}
    return record


def query(text, mode=None, limit=3):
    terms = re.findall(r"[\w\u4e00-\u9fff]+", text.lower())
    ranked = []
    for rule in load("knowledge.json")["rules"]:
        if mode and mode not in rule["modes"]:
            continue
        tag_hits = {t for t in rule["tags"] if t.lower() in text.lower()}
        hay = " ".join(str(v) for k, v in rule.items() if k not in ("dois", "method_sections")).lower()
        term_hits = {t for t in terms if t in hay}
        score = len(tag_hits) * 3 + len(term_hits)
        if score:
            ranked.append((score, rule, bool(tag_hits)))
    # Prefer explicit domain tags; only fall back to incidental body matches
    # when no tagged rule applies. Do not fill a result quota with weak matches.
    if any(pair[2] for pair in ranked):
        ranked = [pair for pair in ranked if pair[2]]
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    results = []
    for score, rule, tagged in ranked[:limit]:
        item = dict(rule)
        item["lexical_score_not_evidence_quality"] = score
        item["explicit_tag_match"] = tagged
        item["sources"] = [{"doi": d, "registry_status": source(d).get("eligibility", source(d).get("status", "quarantine"))} for d in rule["dois"]]
        results.append(item)
    return {"query": text, "mode": mode, "retrieval": "deterministic lexical match; not semantic search or citation verification", "results": results}


def palette_data(identifier, labels=None):
    palette = next((r for r in load("colors.json")["palettes"] if r["id"].lower() == identifier.lower()), None)
    if palette is None:
        raise ValueError("Unknown palette ID; no invented source colors.")
    if labels is not None:
        if identifier.lower() != "soc":
            raise ValueError("Custom state labels apply to the soc palette only.")
        names = [s.strip() for s in labels.split(",")]
        if not 1 <= len(names) <= 5 or any(not s or len(s) > 48 for s in names) or len(set(names)) != len(names):
            raise ValueError("Use 1..5 unique, nonempty state labels (at most 48 characters each).")
        original = palette["role_map"]
        indices = [2] if len(names) == 1 else [round(i*4/(len(names)-1)) for i in range(len(names))]
        palette["role_map"] = [dict(original[i], role=label, state_label=label) for i, label in zip(indices, names)]
        palette["state_labels_are_user_defined_not_measured_by_tool"] = True
        # Label order alone does not establish SOC/Na content or branch equivalence.
        palette["encoding"] = "discrete_state_identity_not_quantitative_soc"
        palette["physical_state_order_verified"] = False
        palette["purpose"] = "离散状态身份示例：明暗不表示SOC/Na含量大小；输入次序不是物理顺序。"
        palette["conditions"] = "必须直标状态和充放分支；此返回仅编码身份。若希望颜色编码SOC/Na量，先确认实际数值和排序，同SOC须同色，充放分支用线型或空心/实心区分。未确认时优先分面，不能把当前明暗解释为递增SOC。"
        selected = {row["hex"] for row in palette["role_map"]}
        preflight = palette["numerical_preflight"]
        preflight["colors"] = [row for row in preflight["colors"] if row["hex"] in selected]
        preflight["scope"] = "returned_colors_only_not_physical_state_order"
    return palette


def audit(task):
    errors, review = [], []
    def flag(code, where, message, error=False):
        (errors if error else review).append({"code": code, "where": where, "message": message})
    if not isinstance(task, dict):
        return {"errors": [{"code": "SHAPE", "message": "Task must be an object."}], "review_items": [], "scientific_acceptance": False}
    tables = {}
    for name in ("conditions", "evidence", "claims", "figures"):
        records = task.get(name, [])
        if not isinstance(records, list):
            flag("SHAPE", name, "Expected an array.", True)
            records = []
        table = {}
        for rec in records:
            if not isinstance(rec, dict) or not isinstance(rec.get("id"), str) or not rec["id"]:
                flag("MISSING_ID", name, "Each record needs a nonempty string id.", True)
                continue
            if rec["id"] in table:
                flag("DUPLICATE_ID", name, rec["id"], True)
            table[rec["id"]] = rec
        tables[name] = table
    conditions, evidence, claims, figures = (tables[n] for n in ("conditions", "evidence", "claims", "figures"))
    if not any(tables.values()):
        flag("EMPTY_TASK", "task", "No evidence or figure plan supplied; no meaningful audit possible.", True)

    def refs(rec, key, table, where):
        values = rec.get(key, [])
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            flag("SHAPE", where, key + " must be an array of IDs.", True)
            return []
        for value in values:
            if value not in table:
                flag("UNKNOWN_REFERENCE", where, key + ": " + value, True)
        return [table[v] for v in values if v in table]

    for eid, ev in evidence.items():
        for field in ("method", "origin_id", "condition_id", "inspection"):
            if not isinstance(ev.get(field), str) or not ev[field]:
                flag("EVIDENCE_FIELD", eid, "Missing " + field, True)
        if ev.get("condition_id") not in conditions:
            flag("UNKNOWN_CONDITION", eid, str(ev.get("condition_id")), True)
        if ev.get("inspection") not in ("text", "caption", "image", "raw", "model"):
            flag("INSPECTION", eid, "Use text/caption/image/raw/model.", True)
        if ev.get("source_doi"):
            if not isinstance(ev["source_doi"], str):
                flag("DOI_TYPE", eid, "DOI must be a string.", True)
            elif source(ev["source_doi"]).get("quarantine"):
                flag("QUARANTINED_SOURCE", eid, "Do not use this entry's scientific content.", True)

    for cid, claim in claims.items():
        linked = refs(claim, "evidence_ids", evidence, cid)
        if claim.get("requires_literature_support"):
            source_dois = [e.get("source_doi") for e in linked if e.get("source_doi")]
            if not source_dois:
                if claim.get("basis") == "general_theory" and claim.get("support_note"):
                    flag("THEORY_WITHOUT_DIRECT_PAPER", cid, "Keep the no-direct-literature-support label beside this conditional inference; not a proven mechanism.")
                else:
                    flag("MISSING_SUPPORTING_DOI", cid, "Evidence-dependent innovation/argument needs a matched supporting DOI and support scope, or explicit theory-only fallback.")
            elif not claim.get("support_scope"):
                flag("DOI_SUPPORT_SCOPE", cid, "State what each cited paper supports; DOI presence alone does not verify claim support.")
        if claim.get("kind") not in ("observation", "inference", "hypothesis", "causal"):
            flag("CLAIM_KIND", cid, "Use observation/inference/hypothesis/causal.", True)
        if not claim.get("text"):
            flag("CLAIM_TEXT", cid, "Claim text is missing.", True)
        if not linked and claim.get("kind") != "hypothesis":
            flag("UNSUPPORTED_CLAIM", cid, "No linked evidence; keep as hypothesis or supply evidence.")
        origins = {e.get("origin_id") for e in linked if e.get("origin_id")}
        count = claim.get("asserted_independent_sources")
        if count is not None:
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                flag("INDEPENDENCE_TYPE", cid, "Expected a nonnegative integer.", True)
            elif count > len(origins):
                flag("DEPENDENT_EVIDENCE", cid, "Claimed independent sources exceed distinct supplied raw-data origins.")
        if claim.get("kind") == "causal":
            if not claim.get("controls"):
                flag("CAUSAL_CONTROL", cid, "A causal claim needs an explicitly described discriminating control; a string is not proof of adequacy.")
            if not claim.get("alternatives"):
                flag("CAUSAL_ALTERNATIVES", cid, "Consider credible alternatives; do not invent them to satisfy a quota.")
        methods = {str(e.get("method", "")).lower() for e in linked}
        if claim.get("quantity") == "intrinsic_electronic_conductivity" and methods and methods <= {"dos", "pdos", "dft bandgap", "optical bandgap", "ups"}:
            flag("PROXY_OVERREACH", cid, "Electronic-state proxies alone do not measure intrinsic electronic conductivity.")
        if claim.get("quantity") in ("paper_color", "visual_layout") and not any(e.get("inspection") in ("image", "raw") for e in linked):
            flag("VISUAL_UNVERIFIED", cid, "Text/captions cannot establish source colors or layout quality.")

    panels = {}
    palette = task.get("palette") or {}
    if not isinstance(palette, dict):
        flag("SHAPE", "palette", "Expected an object.", True)
        palette = {}
    roles = palette.get("roles") or {}
    if not isinstance(roles, dict):
        flag("SHAPE", "palette.roles", "Expected role to HEX mapping.", True)
        roles = {}
    for role, hexcode in roles.items():
        if not isinstance(hexcode, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", hexcode):
            flag("INVALID_HEX", "palette.roles", str(role), True)
    seen_roles = {}
    for fid, fig in figures.items():
        if not fig.get("question"):
            flag("FIGURE_QUESTION", fid, "Define the question this figure answers.")
        refs(fig, "claim_ids", claims, fid)
        items = fig.get("panels", [])
        if not isinstance(items, list):
            flag("SHAPE", fid, "panels must be an array.", True)
            continue
        for panel in items:
            if not isinstance(panel, dict) or not isinstance(panel.get("id"), str) or not panel["id"]:
                flag("PANEL_ID", fid, "Panel needs a nonempty string ID.", True)
                continue
            pid = panel["id"]
            if pid in panels:
                flag("DUPLICATE_PANEL", pid, "Panel IDs must be globally unique.", True)
            panels[pid] = panel
            linked = refs(panel, "evidence_ids", evidence, pid)
            if not linked:
                flag("PANEL_NO_EVIDENCE", pid, "Distinguish conceptual panel from measured evidence.")
            state = panel.get("condition_id")
            if state not in conditions:
                flag("UNKNOWN_CONDITION", pid, str(state), True)
            if any(e.get("condition_id") != state for e in linked):
                flag("PANEL_STATE_CONFLICT", pid, "Panel condition differs from linked evidence; split or explicitly label multi-state content.")
            role, color = panel.get("color_role"), panel.get("hex")
            if color and (not isinstance(color, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color)):
                flag("INVALID_HEX", pid, str(color), True)
            elif role and color:
                if role in roles and roles[role].lower() != color.lower():
                    flag("COLOR_ROLE_CONFLICT", pid, "Panel color disagrees with declared role dictionary.")
                if role in seen_roles and seen_roles[role].lower() != color.lower():
                    flag("COLOR_ROLE_DRIFT", pid, "Same role changes color across panels.")
                seen_roles[role] = color
    comparisons = task.get("comparisons", [])
    if not isinstance(comparisons, list):
        flag("SHAPE", "comparisons", "Expected an array.", True)
        comparisons = []
    for comp in comparisons:
        if not isinstance(comp, dict):
            flag("SHAPE", "comparison", "Expected an object.", True)
            continue
        cid = comp.get("id", "comparison")
        linked = refs(comp, "panel_ids", panels, cid)
        states = [conditions[p["condition_id"]] for p in linked if p.get("condition_id") in conditions]
        fields = ["temperature_c", "voltage_window", "mass_basis"]
        if comp.get("purpose") == "same_state":
            fields += ["soc", "cycle"]
        skip = comp.get("intentional_differences", [])
        if not isinstance(skip, list) or not all(isinstance(v, str) for v in skip):
            flag("SHAPE", cid, "intentional_differences must be a string array.", True)
            skip = []
        for key in fields:
            vals = [s.get(key) for s in states]
            if not vals or any(v is None or v == "" for v in vals):
                flag("COMPARISON_UNKNOWN", cid, key + " missing; no equivalence certified.")
            elif len({json.dumps(v, sort_keys=True) for v in vals}) > 1:
                code = "DECLARED_CONDITION_DIFFERENCE" if key in skip else "CONDITION_MISMATCH"
                flag(code, cid, key + " differs; state the comparison's limited scope.")
    if palette.get("exact_author_colors"):
        flag("UNVERIFIED_SOURCE_RGB", "palette", "This skill has no certified exact author RGB values.")
    if palette.get("kind") in ("sequential", "diverging"):
        bar = palette.get("colorbar") or {}
        if not isinstance(bar, dict):
            bar = {}
        if any(bar.get(k) is None for k in ("vmin", "vmax", "unit")):
            flag("COLORBAR_UNKNOWN", "palette", "Continuous color needs numeric range, units and explicit normalization.")
        if palette.get("kind") == "diverging" and bar.get("center") is None:
            flag("COLORBAR_CENTER", "palette", "Name the physical reference/zero for a diverging map.")
    return {"errors": errors, "review_items": review, "scientific_acceptance": False, "note": "Finite structural preflight only; no scientific validation, reproducibility or visual certification."}


def luminance(hexcode):
    rgb = [int(hexcode[i:i+2], 16)/255 for i in (1, 3, 5)]
    linear = [x/12.92 if x <= .04045 else ((x+.055)/1.055)**2.4 for x in rgb]
    return sum(x*w for x, w in zip(linear, (.2126, .7152, .0722)))


def swatches(path):
    """Render original role examples, not experimental curves or paper artwork."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1160" height="850" viewBox="0 0 1160 850">', '<rect width="1160" height="850" fill="#FFFFFF"/>', '<g font-family="Segoe UI,Arial,sans-serif" fill="#2F3438">', '<text x="40" y="42" font-size="24" font-weight="600">Original color grammar: roles before decoration</text>', '<text x="40" y="69" font-size="14">Design examples only. Not source-paper RGB, experimental data, or accessibility certification.</text>']
    titles = {"dual":"Two samples: stable identity + redundant markers", "categorical":"Four controls: fixed order, no implied performance rank", "soc":"Ordered states: explicit state ID + charge/discharge branch", "sequential":"Nonnegative quantity: shared range, units and normalization", "diverging":"Signed difference: fixed physical zero; missing values separate"}
    dash = {"solid":"", "dash":"9 5", "dot":"2 5", "dash-dot":"9 4 2 4"}
    for i, palette in enumerate(load("colors.json")["palettes"]):
        y = 112 + i*139
        parts.append(f'<text x="40" y="{y}" font-size="18" font-weight="600">{html.escape(titles[palette["id"]])}</text>')
        rows = palette.get("role_map", palette.get("stops", []))
        for j, row in enumerate(rows):
            x = 45 + j*213
            color = row["hex"]
            label = row.get("role", str(row.get("position", "")))
            if "role" in row:
                d = dash.get(row.get("line"), "")
                parts.append(f'<path d="M{x} {y+30} H{x+163}" stroke="{color}" stroke-width="3" stroke-dasharray="{d}"/>')
                cx, cy = x+81, y+30
                fill = "#FFFFFF" if row.get("fill") == "open" else color
                marker = row.get("marker", "circle")
                if marker == "circle":
                    parts.append(f'<circle cx="{cx}" cy="{cy}" r="6" fill="{fill}" stroke="{color}" stroke-width="2"/>')
                else:
                    shapes = {"square":[(-6,-6),(6,-6),(6,6),(-6,6)], "triangle":[(0,-7),(7,6),(-7,6)], "diamond":[(0,-8),(7,0),(0,8),(-7,0)], "pentagon":[(0,-8),(8,-2),(5,7),(-5,7),(-8,-2)]}
                    points = " ".join(f"{cx+dx},{cy+dy}" for dx,dy in shapes.get(marker, shapes["square"]))
                    parts.append(f'<polygon points="{points}" fill="{fill}" stroke="{color}" stroke-width="2"/>')
            else:
                parts.append(f'<rect x="{x}" y="{y+16}" width="180" height="29" fill="{color}" stroke="#CBD1D6"/>')
            parts.append(f'<text x="{x}" y="{y+66}" font-size="13">{html.escape(label)}</text><text x="{x}" y="{y+86}" font-size="13">{color}</text>')
    parts += ['<text x="40" y="823" font-size="13">Use real data for final-size QA. State colors are not sample colors; light map stops are not text colors.</text>', '</g></svg>']
    with path.open("x", encoding="utf-8") as handle:
        handle.write("\n".join(parts))
    return {"output": str(path.resolve()), "original_proposal": True, "copyright_images": False}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    q = subs.add_parser("query")
    q.add_argument("text")
    q.add_argument("--mode", choices=["innovation","methods","logic","figures","colors","reading"])
    q.add_argument("--limit", type=int, default=3)
    for command in ("show", "arc", "source", "observed"):
        subs.add_parser(command).add_argument("id")
    pal = subs.add_parser("palette")
    pal.add_argument("id")
    pal.add_argument("--labels", help="For soc only: comma-separated actual state IDs, not inferred SOC percentages.")
    subs.add_parser("audit").add_argument("file")
    subs.add_parser("swatches").add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "query":
            if not 1 <= args.limit <= 12:
                raise ValueError("limit must be 1..12")
            result = query(args.text, args.mode, args.limit)
        elif args.command in ("show", "arc"):
            field = "rules" if args.command == "show" else "arcs"
            result = next((r for r in load("knowledge.json")[field] if r["id"].lower() == args.id.lower()), None)
            if result is None:
                raise ValueError("Unknown ID; do not invent a match.")
        elif args.command == "source":
            result = source(args.id)
        elif args.command == "palette":
            result = palette_data(args.id, args.labels)
        elif args.command == "observed":
            result = next((r for r in load("colors.json")["observed"] if r["entry_id"].lower() == args.id.lower() or r.get("doi") == normalize_doi(args.id)), None)
            if result is None:
                raise ValueError("No recorded match. Absence is not permission to infer paper colors.")
        elif args.command == "audit":
            result = audit(json.loads(Path(args.file).read_text(encoding="utf-8-sig")))
            emit(result)
            return 2 if result["errors"] else 0
        else:
            result = swatches(args.output)
        emit(result)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        emit({"error": str(exc), "scientific_acceptance": False})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
