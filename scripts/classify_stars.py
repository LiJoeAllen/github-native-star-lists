#!/usr/bin/env python3
"""Classify GitHub starred repositories into native Star List targets.

This is the cheap first pass: it uses repository metadata and a user-reviewed
taxonomy JSON. Repos with weak or conflicting evidence are sent to
Review-Inbox-Manual for human review or a later README/content pass.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_OUTDIR = Path.home() / ".cache" / "github-native-star-lists"
REVIEW_LIST = "Review-Inbox-Manual"


@dataclass(frozen=True)
class Rule:
    topics: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    phrases: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()


def parse_json_stream(text: str) -> list[Any]:
    decoder = json.JSONDecoder()
    pos = 0
    items: list[Any] = []
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        value, pos = decoder.raw_decode(text, pos)
        if isinstance(value, list):
            items.extend(value)
        else:
            items.append(value)
    return items


def load_taxonomy(path: Path) -> dict[str, Rule]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("lists", data if isinstance(data, list) else [])
    if not isinstance(entries, list):
        raise ValueError("taxonomy JSON must be a list or an object with a 'lists' array")

    rules: dict[str, Rule] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name or name == REVIEW_LIST:
            continue
        rules[name] = Rule(
            topics=tuple(str(item).strip() for item in entry.get("topics", []) if str(item).strip()),
            terms=tuple(str(item).strip() for item in entry.get("terms", []) if str(item).strip()),
            phrases=tuple(str(item).strip() for item in entry.get("phrases", []) if str(item).strip()),
            languages=tuple(str(item).strip() for item in entry.get("languages", []) if str(item).strip()),
        )
    if not rules:
        raise ValueError("taxonomy JSON produced no usable list rules")
    return rules


def normalize_repo_item(item: dict[str, Any]) -> dict[str, Any]:
    repo = item.get("repo", item)
    topics = repo.get("topics") or []
    if isinstance(topics, str):
        topics = [part.strip() for part in topics.split(",") if part.strip()]
    return {
        "starred_at": item.get("starred_at") or repo.get("starred_at") or "",
        "full_name": repo.get("full_name") or "",
        "html_url": repo.get("html_url") or "",
        "description": repo.get("description") or "",
        "topics": topics,
        "language": repo.get("language") or "",
        "stargazers_count": repo.get("stargazers_count") or "",
        "archived": repo.get("archived") if repo.get("archived") is not None else "",
        "pushed_at": repo.get("pushed_at") or "",
        "default_branch": repo.get("default_branch") or "",
    }


def tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9][a-z0-9+#.-]*", text.casefold()))


def score_category(repo: dict[str, Any], category: str, rule: Rule) -> tuple[float, list[str], bool]:
    full_name = repo["full_name"]
    name_text = full_name.split("/", 1)[-1].replace("-", " ").replace("_", " ")
    description = repo["description"]
    topics = {str(topic).casefold() for topic in repo["topics"]}
    language = repo["language"]
    name_tokens = tokenize(name_text)
    desc_tokens = tokenize(description)
    desc_text = description.casefold()

    score = 0.0
    evidence: list[str] = []
    language_only = True

    for topic in rule.topics:
        if topic.casefold() in topics:
            score += 0.5
            evidence.append(f"topic:{topic}")
            language_only = False

    for term in rule.terms:
        key = term.casefold()
        if key in name_tokens:
            score += 0.35
            evidence.append(f"name:{term}")
            language_only = False
        elif key in desc_tokens:
            score += 0.25
            evidence.append(f"description:{term}")
            language_only = False

    for phrase in rule.phrases:
        if phrase.casefold() in desc_text:
            score += 0.35
            evidence.append(f"phrase:{phrase}")
            language_only = False

    if language and language in rule.languages:
        score += 0.12
        evidence.append(f"language:{language}")

    if language_only:
        score = min(score, 0.35)

    return min(score, 1.0), evidence[:6], language_only


def confidence_for(score: float, proposed_list: str) -> str:
    if proposed_list == REVIEW_LIST:
        return "Low"
    if score >= 0.75:
        return "High"
    if score >= 0.45:
        return "Medium"
    return "Low"


def classify_repo(repo: dict[str, Any], args: argparse.Namespace, rules: dict[str, Rule]) -> dict[str, Any]:
    scored: list[tuple[str, float, list[str], bool]] = []
    for category, rule in rules.items():
        score, evidence, language_only = score_category(repo, category, rule)
        if score > 0:
            scored.append((category, score, evidence, language_only))
    scored.sort(key=lambda item: item[1], reverse=True)

    if not scored or scored[0][1] < args.review_threshold:
        reason = "No strong metadata signal; needs manual review."
        return make_row(repo, REVIEW_LIST, "Low", scored[0][1] if scored else 0.0, reason)

    top_category, top_score, top_evidence, _ = scored[0]
    if args.repo_list_mode == "single":
        reason = format_reason([(top_category, top_score, top_evidence)])
        return make_row(repo, top_category, confidence_for(top_score, top_category), top_score, reason)

    selected = [
        item
        for item in scored
        if item[1] >= args.multi_threshold and not item[3]
    ][: args.max_lists_per_repo]
    if not selected:
        selected = [scored[0]]

    proposed = "|".join(item[0] for item in selected)
    score = selected[0][1]
    reason = format_reason([(item[0], item[1], item[2]) for item in selected])
    return make_row(repo, proposed, confidence_for(score, proposed), score, reason)


def format_reason(items: list[tuple[str, float, list[str]]]) -> str:
    parts = []
    for category, score, evidence in items:
        ev = ", ".join(evidence) if evidence else "weak metadata signal"
        parts.append(f"{category} score={score:.2f} ({ev})")
    return "; ".join(parts)


def make_row(repo: dict[str, Any], proposed_list: str, confidence: str, score: float, reason: str) -> dict[str, Any]:
    return {
        "full_name": repo["full_name"],
        "proposed_list": proposed_list,
        "confidence": confidence,
        "confidence_score": f"{score:.2f}",
        "reason": reason,
        "description": repo["description"],
        "topics": "|".join(str(topic) for topic in repo["topics"]),
        "language": repo["language"],
        "starred_at": repo["starred_at"],
        "html_url": repo["html_url"],
        "stargazers_count": repo["stargazers_count"],
        "archived": repo["archived"],
        "pushed_at": repo["pushed_at"],
        "default_branch": repo["default_branch"],
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "full_name",
        "proposed_list",
        "confidence",
        "confidence_score",
        "reason",
        "description",
        "topics",
        "language",
        "starred_at",
        "html_url",
        "stargazers_count",
        "archived",
        "pushed_at",
        "default_branch",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_summary(path: Path, rows: list[dict[str, Any]], args: argparse.Namespace) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    confidence_counts = Counter(row["confidence"] for row in rows)
    list_counts: Counter[str] = Counter()
    for row in rows:
        for name in row["proposed_list"].split("|"):
            list_counts[name] += 1
    low_rows = [row for row in rows if row["confidence"] == "Low" or row["proposed_list"] == REVIEW_LIST]

    lines = [
        "# GitHub Star Lists classification summary",
        "",
        f"- Total repos: {len(rows)}",
        f"- Repo list mode: {args.repo_list_mode}",
        f"- Taxonomy: {args.taxonomy_json}",
        f"- Review threshold: {args.review_threshold:.2f}",
        f"- Multi threshold: {args.multi_threshold:.2f}",
        "",
        "## Confidence",
    ]
    for key in ("High", "Medium", "Low"):
        lines.append(f"- {key}: {confidence_counts.get(key, 0)}")
    lines.extend(["", "## Lists"])
    for name, count in sorted(list_counts.items(), key=lambda item: (-item[1], item[0].casefold())):
        lines.append(f"- {name}: {count}")
    lines.extend(["", "## Manual review"])
    for row in low_rows[:50]:
        lines.append(f"- {row['full_name']}: {row['proposed_list']} ({row['reason']})")
    if len(low_rows) > 50:
        lines.append(f"- ... {len(low_rows) - 50} more")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Classify GitHub starred repos into native Star List targets")
    ap.add_argument("--input-json", type=Path, required=True, help="Output from gh api --paginate /user/starred")
    ap.add_argument("--taxonomy-json", type=Path, required=True, help="User-reviewed taxonomy JSON from discover_taxonomy.py or a custom file")
    ap.add_argument("--repo-list-mode", choices=["single", "multi"], default="single")
    ap.add_argument("--review-threshold", type=float, default=0.35)
    ap.add_argument("--multi-threshold", type=float, default=0.55)
    ap.add_argument("--max-lists-per-repo", type=int, default=3)
    ap.add_argument("--output-csv", type=Path, default=DEFAULT_OUTDIR / "stars_lists_with_confidence_latest.csv")
    ap.add_argument("--low-confidence-csv", type=Path, default=DEFAULT_OUTDIR / "low_confidence_review_latest.csv")
    ap.add_argument("--summary-md", type=Path, default=DEFAULT_OUTDIR / "classification_summary_latest.md")
    args = ap.parse_args()

    rules = load_taxonomy(args.taxonomy_json)
    raw_items = parse_json_stream(args.input_json.read_text(encoding="utf-8"))
    repos = [normalize_repo_item(item) for item in raw_items if isinstance(item, dict)]
    repos = [repo for repo in repos if repo["full_name"]]
    rows = [classify_repo(repo, args, rules) for repo in repos]
    low_rows = [row for row in rows if row["confidence"] == "Low" or row["proposed_list"] == REVIEW_LIST]

    write_csv(args.output_csv, rows)
    write_csv(args.low_confidence_csv, low_rows)
    write_summary(args.summary_md, rows, args)

    print(f"Repos classified: {len(rows)}")
    print(f"Output CSV: {args.output_csv}")
    print(f"Low-confidence CSV: {args.low_confidence_csv}")
    print(f"Summary: {args.summary_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
