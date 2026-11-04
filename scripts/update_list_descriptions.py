#!/usr/bin/env python3
"""Update GitHub native Star List descriptions through GraphQL.

Reads a JSON mapping of list names to descriptions, updates matching lists
while preserving their names and privacy settings, and verifies the result
by reading GitHub back.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any


def run_json(cmd: list[str], *, stdin: dict[str, Any] | None = None) -> Any:
    proc = subprocess.run(
        cmd,
        input=json.dumps(stdin, ensure_ascii=False) if stdin is not None else None,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"{' '.join(cmd)} failed: {detail}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{' '.join(cmd)} returned non-JSON output") from exc


def gh_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    data = run_json(
        ["gh", "api", "graphql", "--input", "-"],
        stdin={"query": query, "variables": variables},
    )
    if data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"], ensure_ascii=False))
    return data["data"]


def infer_username() -> str:
    proc = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"Could not infer GitHub username: {detail}")
    login = proc.stdout.strip()
    if not login:
        raise RuntimeError("Could not infer GitHub username from gh")
    return login


def load_descriptions(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "lists" in data:
        entries = data["lists"]
        if not isinstance(entries, list):
            raise ValueError("descriptions JSON 'lists' must be an array")
    elif isinstance(data, dict):
        entries = [
            {"name": name, "description": description}
            for name, description in data.items()
        ]
    else:
        raise ValueError("descriptions JSON must be an object")

    descriptions: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        description = str(entry.get("description", "")).strip()
        if not name or not description:
            continue
        if name in descriptions:
            raise ValueError(f"duplicate list name in descriptions JSON: {name}")
        descriptions[name] = description
    if not descriptions:
        raise ValueError("descriptions JSON produced no usable name/description pairs")
    return descriptions


def fetch_lists(username: str) -> list[dict[str, Any]]:
    query = """
    query($login: String!, $after: String) {
      user(login: $login) {
        lists(first: 100, after: $after) {
          nodes { id name description isPrivate }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
    """
    lists: list[dict[str, Any]] = []
    after = None
    while True:
        data = gh_graphql(query, {"login": username, "after": after})
        conn = data["user"]["lists"]
        lists.extend(conn["nodes"])
        if not conn["pageInfo"]["hasNextPage"]:
            break
        after = conn["pageInfo"]["endCursor"]
    return lists


def update_list(item: dict[str, Any], description: str) -> dict[str, Any]:
    query = """
    mutation($listId: ID!, $name: String!, $description: String!, $isPrivate: Boolean!) {
      updateUserList(input: {listId: $listId, name: $name, description: $description, isPrivate: $isPrivate}) {
        list { id name description isPrivate }
      }
    }
    """
    data = gh_graphql(
        query,
        {
            "listId": item["id"],
            "name": item["name"],
            "description": description,
            "isPrivate": item["isPrivate"],
        },
    )
    return data["updateUserList"]["list"]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Update GitHub native Star List descriptions from a JSON mapping"
    )
    ap.add_argument(
        "--descriptions-json",
        type=Path,
        required=True,
        help="JSON object mapping list names to descriptions",
    )
    ap.add_argument(
        "--username",
        help="GitHub login; defaults to the authenticated gh account",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned updates without writing",
    )
    ap.add_argument(
        "--sleep",
        type=float,
        default=0.3,
        help="Seconds to wait between mutations",
    )
    args = ap.parse_args()

    descriptions = load_descriptions(args.descriptions_json)
    username = args.username or infer_username()
    lists = fetch_lists(username)
    by_name = {item["name"]: item for item in lists}
    by_casefold: dict[str, dict[str, Any]] = {}
    for item in lists:
        key = item["name"].casefold()
        if key in by_casefold:
            raise RuntimeError(f"Ambiguous list names differ only by case: {item['name']}")
        by_casefold[key] = item

    print(f"Account: {username}")
    print(f"Lists found: {len(lists)}")
    print(f"Descriptions loaded: {len(descriptions)}")

    updated = 0
    skipped = 0
    failed = 0
    missing: list[str] = []
    original_privacy: dict[str, bool] = {}

    for name, description in descriptions.items():
        item = by_name.get(name) or by_casefold.get(name.casefold())
        if not item:
            missing.append(name)
            print(f"MISSING: {name}")
            continue
        if item["description"] == description:
            skipped += 1
            print(f"SKIP: {name} (already up to date)")
            continue
        if args.dry_run:
            updated += 1
            print(f"WOULD UPDATE: {name}")
            continue
        original_privacy[item["name"]] = bool(item["isPrivate"])
        try:
            update_list(item, description)
            updated += 1
            print(f"UPDATED: {name}")
        except Exception as exc:
            failed += 1
            print(f"FAILED: {name}: {exc}")
        time.sleep(args.sleep)

    if args.dry_run:
        print(
            f"\nDry-run only. Would update: {updated}; "
            f"skipped: {skipped}; missing: {len(missing)}"
        )
        return 0 if not missing else 1

    print("\nVerifying...")
    final_lists = fetch_lists(username)
    final_by_name = {item["name"]: item for item in final_lists}
    mismatches: list[str] = []
    privacy_changes: list[str] = []
    for name, description in descriptions.items():
        item = final_by_name.get(name)
        if not item or item["description"] != description:
            mismatches.append(name)
            continue
        if name in original_privacy and bool(item["isPrivate"]) != original_privacy[name]:
            privacy_changes.append(name)

    print(f"Updated: {updated}")
    print(f"Skipped: {skipped}")
    print(f"Failed: {failed}")
    print(f"Missing: {len(missing)}")
    print(f"Verified descriptions: {len(descriptions) - len(mismatches)}/{len(descriptions)}")
    if privacy_changes:
        print("Privacy changes: " + ", ".join(privacy_changes))
    if mismatches:
        print("Mismatches: " + ", ".join(mismatches))
    return 0 if not failed and not missing and not mismatches and not privacy_changes else 1


if __name__ == "__main__":
    raise SystemExit(main())
