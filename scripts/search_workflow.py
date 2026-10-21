#!/usr/bin/env python3
"""Offline search planning and candidate deduplication; never claims live search."""
import argparse
from datetime import date
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import mentor

FAMILIES = {
    "nfpp": ["NFPP", "NFMPP", "NMFPP", "Na4Fe3(PO4)2(P2O7)", "mixed phosphate pyrophosphate"],
    "nasicon": ["NASICON", "NASCION", "Na3V2(PO4)3", "sodium superionic conductor cathode"],
    "phosphate": ["sodium phosphate cathode", "NaFePO4", "transition metal phosphate"],
    "pyrophosphate": ["sodium pyrophosphate", "Na2FeP2O7", "diphosphate cathode"],
    "fluorophosphate": ["sodium fluorophosphate", "Na3V2(PO4)2F3", "NVPF"],
    "sulfate": ["sodium sulfate cathode", "Na2Fe2(SO4)3", "alluaudite"]
}


def iso(value):
    return date.fromisoformat(value)


def window(start, end):
    if bool(start) != bool(end):
        raise ValueError("Provide both --from-date and --until-date, or neither for screening.")
    if start and iso(start) > iso(end):
        raise ValueError("Start date must not be after end date.")


def plan(families, start, end, journals=None, seeds=None):
    window(start, end)
    if not start:
        raise ValueError("A plan needs explicit dates; do not silently reuse the historical window.")
    queries = []
    for family in dict.fromkeys(families):
        aliases = FAMILIES[family]
        base = '(sodium OR "Na-ion") (cathode OR "positive electrode") (' + " OR ".join('"'+a+'"' for a in aliases) + ')'
        for purpose, extra in [
            ("broad_material_recall", ""),
            ("modification_families", " (doping OR substitution OR vacancy OR defect OR coating OR composite OR morphology OR synthesis)"),
            ("mechanism_and_failure", " (operando OR phase OR transport OR degradation OR stability)")
        ]:
            queries.append({"id": f"{family}-{purpose}", "family": family, "purpose": purpose, "query_concept": base+extra, "status": "planned_not_run"})
    return {
        "schema_version": 1, "execution_status": "planned_only_no_network", "executed_queries": 0,
        "scope": {"system": "sodium-ion polyanionic cathode modification", "families": list(dict.fromkeys(families)), "first_online_from": start, "first_online_until": end, "journals_user_supplied": journals or [], "excluded_objects": ["standalone anode", "standalone electrolyte"]},
        "query_matrix": queries,
        "query_syntax_notice": "Conceptual strings only: adapt syntax to the actual source; no API or endpoint is assumed.",
        "source_routes": ["publisher primary pages", "currently available scholarly metadata source", "included-seed citation chaining", "verified journal/ISSN by time cells when coverage is requested"],
        "known_item_recall": {"seed_dois": [doi_key(d) for d in seeds or []], "status": "not_tested"},
        "execution_ledger_fields": ["query_id", "source", "executed_at", "page_or_cursor", "actual_access_state", "result_count", "raw_locator", "raw_sha256_if_available"],
        "screening_fields": ["doi", "title", "journal", "first_online", "article_type", "cathode_object", "modification_relevance", "eligibility_reason", "inspection_status", "source_locator"],
        "stop_rule": "Use the user's task boundary; distinguish searched cells, unique metadata, screened eligibility and completed full text. Missing seeds/access remain gaps.",
        "exhaustive_coverage_certified": False, "new_papers_read": 0
    }


def doi_key(raw):
    if not isinstance(raw, str):
        return None
    value = mentor.normalize_doi(unquote(raw))
    return value if re.fullmatch(r"10\.\d{4,9}/\S+", value) else None


def screen(payload, start=None, end=None):
    window(start, end)
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError("Expected an object with records array.")
    buckets, missing = {}, []
    for idx, rec in enumerate(payload["records"]):
        if not isinstance(rec, dict):
            raise ValueError(f"records[{idx}] must be an object")
        doi = doi_key(rec.get("doi"))
        if not doi:
            missing.append({"input_index": idx, "title": rec.get("title"), "source": rec.get("source"), "locator": rec.get("locator"), "next": "resolve DOI; keep separate, do not silently title-merge"})
        else:
            buckets.setdefault(doi, []).append((idx, rec))
    rows = []
    title_keys = {}
    for doi, group in buckets.items():
        historical = mentor.source(doi)
        quarantined = historical.get("quarantine", False)
        titles = list(dict.fromkeys(r["title"] for _, r in group if isinstance(r.get("title"), str) and r["title"].strip()))
        rawdates = [r.get("first_online") for _, r in group]
        known_dates, invalid_dates = set(), []
        for raw in rawdates:
            if raw is None or raw == "":
                continue
            try:
                known_dates.add(iso(raw))
            except (ValueError, TypeError):
                invalid_dates.append(raw)
        flags = []
        if len(titles) > 1:
            flags.append("title_conflict_requires_identity_check")
        if len(known_dates) > 1:
            flags.append("first_online_conflict_do_not_choose_convenient_date")
        if invalid_dates:
            flags.append("invalid_date_field")
        if not known_dates:
            date_status = "unknown_first_online"
        elif len(known_dates) != 1 or invalid_dates:
            date_status = "date_unresolved"
        elif start:
            d = next(iter(known_dates))
            date_status = "inside_requested_window_metadata_only" if iso(start) <= d <= iso(end) else "outside_requested_window_metadata_only"
        else:
            date_status = "date_recorded_no_window_test"
        if quarantined:
            action = "quarantined_metadata_only_do_not_migrate"
        elif historical.get("id"):
            action = "known_identity_check_recorded_gaps_do_not_recount"
        else:
            action = "new_candidate_needs_object_modification_journal_type_and_date_screening"
        origins = [{"input_index": idx, "source": r.get("source"), "locator": r.get("locator")} for idx,r in group]
        rows.append({"doi": doi, "titles": [] if quarantined else titles, "input_occurrences": len(group), "origins": origins, "historical_id": historical.get("id"), "historical_reading": {} if quarantined else historical.get("reading", {}), "quarantine": quarantined, "date_status": date_status, "first_online_candidates": sorted(d.isoformat() for d in known_dates), "flags": flags, "next": action, "eligible_by_full_text": False})
        for title in titles:
            key = re.sub(r"\W+", "", title.lower())
            if key:
                title_keys.setdefault(key, set()).add(doi)
    title_collisions = [sorted(dois) for dois in title_keys.values() if len(dois) > 1]
    return {"schema_version": 1, "execution_status": "offline_metadata_screen_only", "input_records": len(payload["records"]), "unique_valid_dois": len(rows), "duplicate_occurrences": sum(len(v)-1 for v in buckets.values()), "missing_or_invalid_doi_records": missing, "possible_title_collisions_not_merged": title_collisions, "records": rows, "new_papers_read": 0, "exhaustive_coverage_certified": False, "notice": "Date and DOI fields are supplied metadata, not verified primary evidence. No title-based scientific eligibility or novelty verdict."}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--family", action="append", choices=list(FAMILIES), required=True)
    p.add_argument("--from-date", required=True)
    p.add_argument("--until-date", required=True)
    p.add_argument("--journal", action="append")
    p.add_argument("--seed", action="append")
    s = sub.add_parser("screen")
    s.add_argument("file")
    s.add_argument("--from-date")
    s.add_argument("--until-date")
    a = parser.parse_args()
    try:
        result = plan(a.family, a.from_date, a.until_date, a.journal, a.seed) if a.command == "plan" else screen(json.loads(Path(a.file).read_text(encoding="utf-8-sig")), a.from_date, a.until_date)
        mentor.emit(result)
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        mentor.emit({"error": str(exc), "execution_status": "not_executed", "new_papers_read": 0})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
