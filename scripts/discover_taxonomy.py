#!/usr/bin/env python3
"""Discover a draft Star List taxonomy from a user's starred repo metadata."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_OUTDIR = Path.home() / ".cache" / "github-native-star-lists"
STOP_TOPICS = {
    "awesome",
    "example",
    "examples",
    "hacktoberfest",
    "github",
    "open-source",
    "library",
    "tool",
    "tools",
    "app",
    "application",
}


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


def normalize_repo_item(item: dict[str, Any]) -> dict[str, Any]:
    repo = item.get("repo", item)
    topics = repo.get("topics") or []
    if isinstance(topics, str):
        topics = [part.strip() for part in topics.split(",") if part.strip()]
    return {
        "full_name": repo.get("full_name") or "",
        "description": repo.get("description") or "",
        "topics": [str(topic).casefold() for topic in topics if str(topic).strip()],
        "language": repo.get("language") or "",
    }


def list_name_from_key(prefix: str, key: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9+#.-]+", "-", key.strip()).strip("-")
    if not cleaned:
        cleaned = "Uncategorized"
    if prefix:
        return f"{prefix}{cleaned}"
    return cleaned[:1].upper() + cleaned[1:]


def discover(repos: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    topic_counts: Counter[str] = Counter()
    language_counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)

    for repo in repos:
        for topic in repo["topics"]:
            if topic in STOP_TOPICS or len(topic) < 2:
                continue
            topic_counts[topic] += 1
            if len(examples[f"topic:{topic}"]) < 5:
                examples[f"topic:{topic}"].append(repo["full_name"])
        if repo["language"]:
            language_counts[repo["language"]] += 1
            if len(examples[f"language:{repo['language']}"]) < 5:
                examples[f"language:{repo['language']}"].append(repo["full_name"])

    lists: list[dict[str, Any]] = []
    for topic, count in topic_counts.most_common(args.max_topics):
        if count < args.min_topic_count:
            continue
        lists.append({
            "name": list_name_from_key(args.topic_prefix, topic),
            "topics": [topic],
            "terms": [topic],
            "phrases": [],
            "languages": [],
            "source": "topic",
            "repo_count": count,
            "examples": examples[f"topic:{topic}"],
        })

    for language, count in language_counts.most_common(args.max_languages):
        if count < args.min_language_count:
            continue
        lists.append({
            "name": list_name_from_key(args.language_prefix, language),
            "topics": [],
            "terms": [],
            "phrases": [],
            "languages": [language],
            "source": "language",
            "repo_count": count,
            "examples": examples[f"language:{language}"],
        })

    return {
        "version": 1,
        "source": "github-star-metadata-discovery",
        "notes": [
            "This is a draft taxonomy generated from the user's own starred repo metadata.",
            "Review, rename, merge, split, or delete lists before classifying.",
            "Language-only lists are weak signals; prefer topic/term rules for higher-confidence classification.",
        ],
        "lists": lists,
    }


def write_review(path: Path, taxonomy: dict[str, Any]) -> None:
    lines = [
        "# Draft GitHub Star Lists taxonomy",
        "",
        "Review this draft before classification. Rename, merge, split, or delete list entries in the JSON as needed.",
        "",
        "## Lists",
    ]
    for item in taxonomy["lists"]:
        examples = ", ".join(item.get("examples", [])[:3])
        lines.append(f"- {item['name']}: {item['repo_count']} repos, source={item['source']}, examples={examples}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Discover a draft Star List taxonomy from starred repo metadata")
    ap.add_argument("--input-json", type=Path, required=True, help="Output from gh api --paginate /user/starred")
    ap.add_argument("--output-json", type=Path, default=DEFAULT_OUTDIR / "taxonomy_draft_latest.json")
    ap.add_argument("--review-md", type=Path, default=DEFAULT_OUTDIR / "taxonomy_draft_review_latest.md")
    ap.add_argument("--min-topic-count", type=int, default=2)
    ap.add_argument("--min-language-count", type=int, default=5)
    ap.add_argument("--max-topics", type=int, default=40)
    ap.add_argument("--max-languages", type=int, default=12)
    ap.add_argument("--topic-prefix", default="")
    ap.add_argument("--language-prefix", default="Language-")
    args = ap.parse_args()

    raw_items = parse_json_stream(args.input_json.read_text(encoding="utf-8"))
    repos = [normalize_repo_item(item) for item in raw_items if isinstance(item, dict)]
    repos = [repo for repo in repos if repo["full_name"]]
    taxonomy = discover(repos, args)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding="utf-8")
    write_review(args.review_md, taxonomy)

    print(f"Repos scanned: {len(repos)}")
    print(f"Draft lists: {len(taxonomy['lists'])}")
    print(f"Taxonomy JSON: {args.output_json}")
    print(f"Review Markdown: {args.review_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
