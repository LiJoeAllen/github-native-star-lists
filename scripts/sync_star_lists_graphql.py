#!/usr/bin/env python3
"""Sync classified GitHub stars into native Star Lists using GitHub GraphQL.

Default mode is additive:
- create missing lists
- add missing repo/list memberships
- preserve all existing memberships
- never remove repos or delete lists

Writes require an approved dry-run plan:
  python scripts/sync_star_lists_graphql.py --input-csv stars.csv --dry-run
  python scripts/sync_star_lists_graphql.py --apply --plan PLAN.json --confirm-plan-id PLAN_ID
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_OUTDIR = Path.home() / ".cache" / "github-native-star-lists"
CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class RepoTarget:
    full_name: str
    list_name: str


def split_list_names(value: str, separator: str) -> list[str]:
    if separator:
        parts = value.split(separator)
    else:
        parts = value.replace(";", "|").replace(",", "|").split("|")
    return [part.strip() for part in parts if part.strip()]


def run_json(cmd: list[str], *, stdin: dict[str, Any] | None = None) -> Any:
    input_text = json.dumps(stdin) if stdin is not None else None
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
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
    payload = {"query": query, "variables": variables}
    data = run_json(["gh", "api", "graphql", "--input", "-"], stdin=payload)
    if "errors" in data:
        message = json.dumps(data["errors"], ensure_ascii=False)
        if "INSUFFICIENT_SCOPES" in message or "insufficient" in message.lower():
            message += "\nToken is missing a required scope. Authenticate gh with a GitHub token that includes user scope, or run: gh auth refresh -h github.com -s user"
        raise RuntimeError(message)
    return data["data"]


def infer_username() -> str:
    proc = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        text=True,
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


def load_targets(
    path: Path,
    repo_col: str,
    list_col: str,
    skip_lists: set[str],
    confidence_col: str,
    min_confidence: str,
    repo_list_mode: str,
    list_separator: str,
    limit: int,
) -> tuple[list[RepoTarget], list[dict[str, str]]]:
    targets: list[RepoTarget] = []
    skipped: list[dict[str, str]] = []
    min_rank = CONFIDENCE_RANK[min_confidence.casefold()]

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            full = (row.get(repo_col) or "").strip()
            raw_list = (row.get(list_col) or "").strip()
            confidence = (row.get(confidence_col) or "").strip()
            list_names = split_list_names(raw_list, list_separator)
            if not full or "/" not in full or not list_names:
                skipped.append({"full_name": full, "list_name": raw_list, "reason": "missing_repo_or_list"})
                continue
            if confidence and CONFIDENCE_RANK.get(confidence.casefold(), 0) < min_rank:
                skipped.append({"full_name": full, "list_name": raw_list, "reason": f"confidence_below_{min_confidence}"})
                continue
            if repo_list_mode == "single" and len(list_names) > 1:
                for extra in list_names[1:]:
                    skipped.append({"full_name": full, "list_name": extra, "reason": "single_mode_extra_list"})
                list_names = list_names[:1]
            for list_name in list_names:
                if list_name in skip_lists:
                    skipped.append({"full_name": full, "list_name": list_name, "reason": "skip_list"})
                    continue
                targets.append(RepoTarget(full, list_name))
                if limit and len(targets) >= limit:
                    break
            if limit and len(targets) >= limit:
                break

    # De-dupe while preserving order.
    seen: set[tuple[str, str]] = set()
    seen_repo: set[str] = set()
    unique: list[RepoTarget] = []
    for target in targets:
        if repo_list_mode == "single" and target.full_name in seen_repo:
            skipped.append({"full_name": target.full_name, "list_name": target.list_name, "reason": "single_mode_duplicate_repo"})
            continue
        key = (target.full_name, target.list_name)
        if key in seen:
            continue
        seen.add(key)
        seen_repo.add(target.full_name)
        unique.append(target)
    return unique, skipped


def fetch_lists(username: str) -> list[dict[str, Any]]:
    query = """
    query($login: String!, $after: String) {
      user(login: $login) {
        lists(first: 100, after: $after) {
          nodes { id name slug description isPrivate }
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


def fetch_list_items(list_id: str) -> list[dict[str, str]]:
    query = """
    query($id: ID!, $after: String) {
      node(id: $id) {
        ... on UserList {
          items(first: 100, after: $after) {
            nodes {
              ... on Repository { id nameWithOwner }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    }
    """
    items: list[dict[str, str]] = []
    after = None
    while True:
        data = gh_graphql(query, {"id": list_id, "after": after})
        node = data.get("node")
        if not node:
            raise RuntimeError(f"Could not read items for list id {list_id}")
        conn = node["items"]
        for item in conn["nodes"]:
            if item and item.get("nameWithOwner"):
                items.append({"id": item["id"], "full_name": item["nameWithOwner"]})
        if not conn["pageInfo"]["hasNextPage"]:
            break
        after = conn["pageInfo"]["endCursor"]
    return items


def fetch_state(username: str, include_items: bool) -> dict[str, Any]:
    lists = fetch_lists(username)
    by_name = {item["name"]: item for item in lists}
    by_casefold = {item["name"].casefold(): item for item in lists}
    repo_to_list_ids: dict[str, set[str]] = defaultdict(set)
    repo_ids: dict[str, str] = {}
    list_items: dict[str, list[str]] = {}

    if include_items:
        for item in lists:
            repos = fetch_list_items(item["id"])
            list_items[item["name"]] = [repo["full_name"] for repo in repos]
            for repo in repos:
                repo_to_list_ids[repo["full_name"]].add(item["id"])
                repo_ids[repo["full_name"]] = repo["id"]

    return {
        "lists": lists,
        "by_name": by_name,
        "by_casefold": by_casefold,
        "repo_to_list_ids": repo_to_list_ids,
        "repo_ids": repo_ids,
        "list_items": list_items,
    }


def find_list(state: dict[str, Any], list_name: str) -> dict[str, Any] | None:
    return state["by_name"].get(list_name) or state["by_casefold"].get(list_name.casefold())


def fetch_repo_id(full_name: str) -> str:
    owner, name = full_name.split("/", 1)
    query = """
    query($owner: String!, $name: String!) {
      repository(owner: $owner, name: $name) { id nameWithOwner }
    }
    """
    data = gh_graphql(query, {"owner": owner, "name": name})
    repo = data.get("repository")
    if not repo:
        raise RuntimeError(f"Repository not found: {full_name}")
    return repo["id"]


def create_user_list(name: str, description: str, is_private: bool) -> dict[str, Any]:
    query = """
    mutation($name: String!, $description: String!, $isPrivate: Boolean!) {
      createUserList(input: {name: $name, description: $description, isPrivate: $isPrivate}) {
        list { id name slug description isPrivate }
      }
    }
    """
    data = gh_graphql(query, {"name": name, "description": description, "isPrivate": is_private})
    return data["createUserList"]["list"]


def update_repo_lists(repo_id: str, list_ids: list[str], client_id: str) -> None:
    query = """
    mutation($itemId: ID!, $listIds: [ID!]!, $clientMutationId: String!) {
      updateUserListsForItem(input: {itemId: $itemId, listIds: $listIds, clientMutationId: $clientMutationId}) {
        clientMutationId
      }
    }
    """
    gh_graphql(query, {"itemId": repo_id, "listIds": list_ids, "clientMutationId": client_id})


def build_plan(
    username: str,
    source: Path,
    targets: list[RepoTarget],
    skipped: list[dict[str, str]],
    state: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    lists_to_create: list[str] = []
    repo_additions: list[dict[str, str]] = []
    already_assigned: list[dict[str, str]] = []

    for target in targets:
        existing = find_list(state, target.list_name)
        if not existing:
            if target.list_name not in lists_to_create:
                lists_to_create.append(target.list_name)
            repo_additions.append({"full_name": target.full_name, "list_name": target.list_name})
            continue
        current_ids = state["repo_to_list_ids"].get(target.full_name, set())
        if existing["id"] in current_ids:
            already_assigned.append({"full_name": target.full_name, "list_name": target.list_name})
        else:
            repo_additions.append({"full_name": target.full_name, "list_name": target.list_name})

    by_list = Counter(target.list_name for target in targets)
    core = {
        "version": 1,
        "mode": "additive",
        "username": username,
        "source": str(source),
        "repo_col": args.repo_col,
        "list_col": args.list_col,
        "skip_lists": sorted(args.skip_list or []),
        "min_confidence": args.min_confidence,
        "repo_list_mode": args.repo_list_mode,
        "list_separator": args.list_separator,
        "targets": [target.__dict__ for target in targets],
        "skipped": skipped,
        "lists_to_create": sorted(lists_to_create, key=str.casefold),
        "repo_additions": sorted(repo_additions, key=lambda item: (item["list_name"].casefold(), item["full_name"].casefold())),
    }
    plan_id = hashlib.sha256(json.dumps(core, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return {
        **core,
        "plan_id": plan_id,
        "created_at": datetime.now().isoformat(),
        "existing_lists": [{"name": item["name"], "slug": item["slug"], "id": item["id"]} for item in state["lists"]],
        "already_assigned": already_assigned,
        "summary": {
            "target_rows": len(targets),
            "lists_to_create": len(lists_to_create),
            "repo_additions": len(repo_additions),
            "already_assigned": len(already_assigned),
            "skipped": len(skipped),
            "by_list": dict(sorted(by_list.items())),
        },
    }


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def append_checkpoint(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(), **item}, ensure_ascii=False) + "\n")


def load_done(path: Path) -> set[tuple[str, str]]:
    done: set[tuple[str, str]] = set()
    if not path.exists():
        return done
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("status") in {"added", "already"}:
            done.add((item.get("full_name"), item.get("list_name")))
    return done


def dry_run(args: argparse.Namespace) -> int:
    username = args.username or infer_username()
    targets, skipped = load_targets(
        args.input_csv,
        args.repo_col,
        args.list_col,
        set(args.skip_list or []),
        args.confidence_col,
        args.min_confidence,
        args.repo_list_mode,
        args.list_separator,
        args.limit,
    )
    if not targets:
        print("No targets loaded", file=sys.stderr)
        return 1

    print(f"Reading GitHub Star Lists for {username}...")
    state = fetch_state(username, include_items=True)
    plan = build_plan(username, args.input_csv, targets, skipped, state, args)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = args.outdir / f"graphql_sync_plan_{stamp}.json"
    write_json(path, plan)

    summary = plan["summary"]
    print("\nGitHub Star Lists dry-run")
    print(f"Account: {username}")
    print(f"Source: {args.input_csv}")
    print("Mode: additive")
    print(f"Repo list mode: {plan['repo_list_mode']}")
    print(f"Plan id: {plan['plan_id']}")
    print(f"Lists to create: {summary['lists_to_create']}")
    print(f"Repos to add: {summary['repo_additions']}")
    print(f"Already assigned: {summary['already_assigned']}")
    print(f"Skipped: {summary['skipped']}")
    print(f"Plan saved: {path}")
    print("\nApply with:")
    print(f"python scripts/sync_star_lists_graphql.py --apply --plan {path} --confirm-plan-id {plan['plan_id']}")
    return 0


def apply_plan(args: argparse.Namespace) -> int:
    if not args.plan:
        raise SystemExit("--apply requires --plan")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    if args.confirm_plan_id != plan.get("plan_id"):
        raise SystemExit("--apply requires --confirm-plan-id matching the dry-run plan id")

    username = args.username or plan["username"]
    checkpoint = args.checkpoint or (args.outdir / f"graphql_sync_checkpoint_{plan['plan_id']}.jsonl")
    result_path = args.outdir / f"graphql_sync_result_{plan['plan_id']}.json"
    args.outdir.mkdir(parents=True, exist_ok=True)

    print(f"Applying plan {plan['plan_id']} for {username}")
    print(f"Checkpoint: {checkpoint}")

    created: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    state = fetch_state(username, include_items=False)
    for name in plan["lists_to_create"]:
        existing = find_list(state, name)
        if existing:
            append_checkpoint(checkpoint, {"status": "list_already_exists", "list_name": name, "list_id": existing["id"]})
            continue
        try:
            new_list = create_user_list(name, args.list_description, args.private)
            created.append(new_list)
            append_checkpoint(checkpoint, {"status": "list_created", "list_name": name, "list_id": new_list["id"], "slug": new_list["slug"]})
            print(f"Created list: {name}")
            time.sleep(args.sleep)
        except Exception as exc:
            item = {"status": "failed", "operation": "create_list", "list_name": name, "reason": type(exc).__name__, "message": str(exc)[:500]}
            failures.append(item)
            append_checkpoint(checkpoint, item)

    state = fetch_state(username, include_items=True)
    done = load_done(checkpoint)
    additions = [item for item in plan["repo_additions"] if (item["full_name"], item["list_name"]) not in done]

    stats: Counter[str] = Counter()
    repo_ids: dict[str, str] = dict(state["repo_ids"])
    repo_to_list_ids: dict[str, set[str]] = defaultdict(set, {k: set(v) for k, v in state["repo_to_list_ids"].items()})

    for index, item in enumerate(additions, start=1):
        full_name = item["full_name"]
        list_name = item["list_name"]
        target_list = find_list(state, list_name)
        if not target_list:
            res = {"status": "failed", "operation": "add_repo", "full_name": full_name, "list_name": list_name, "reason": "target_list_not_found"}
            failures.append(res)
            append_checkpoint(checkpoint, res)
            stats["failed"] += 1
            continue

        target_id = target_list["id"]
        current_ids = repo_to_list_ids.get(full_name, set())
        if target_id in current_ids:
            res = {"status": "already", "full_name": full_name, "list_name": list_name, "list_id": target_id}
            append_checkpoint(checkpoint, res)
            stats["already"] += 1
            continue

        try:
            repo_id = repo_ids.get(full_name) or fetch_repo_id(full_name)
            repo_ids[full_name] = repo_id
            desired_ids = sorted(current_ids | {target_id})
            update_repo_lists(repo_id, desired_ids, f"{plan['plan_id']}:{index}")
            repo_to_list_ids[full_name] = set(desired_ids)
            res = {"status": "added", "full_name": full_name, "list_name": list_name, "list_id": target_id}
            append_checkpoint(checkpoint, res)
            stats["added"] += 1
        except Exception as exc:
            res = {"status": "failed", "operation": "add_repo", "full_name": full_name, "list_name": list_name, "reason": type(exc).__name__, "message": str(exc)[:500]}
            failures.append(res)
            append_checkpoint(checkpoint, res)
            stats["failed"] += 1

        if index % 10 == 0 or res["status"] == "failed":
            print(f"[{index}/{len(additions)}] stats={dict(stats)} latest={full_name} -> {list_name}: {res['status']}")
        time.sleep(args.sleep)

    print("Verifying by reading GitHub back...")
    verified_state = fetch_state(username, include_items=True)
    verified = 0
    mismatches: list[dict[str, str]] = []
    for item in plan["repo_additions"]:
        target_list = find_list(verified_state, item["list_name"])
        if target_list and target_list["id"] in verified_state["repo_to_list_ids"].get(item["full_name"], set()):
            verified += 1
        else:
            mismatches.append(item)

    result = {
        "plan_id": plan["plan_id"],
        "username": username,
        "created_lists": created,
        "stats": dict(stats),
        "failures": failures[:200],
        "verified_repo_additions": verified,
        "planned_repo_additions": len(plan["repo_additions"]),
        "mismatches": mismatches[:200],
        "checkpoint": str(checkpoint),
    }
    write_json(result_path, result)

    print("\nGitHub Star Lists sync result")
    print(f"Created lists: {len(created)}")
    print(f"Added repos: {stats['added']}")
    print(f"Already correct: {stats['already']}")
    print(f"Failed operations: {stats['failed'] + len([x for x in failures if x.get('operation') == 'create_list'])}")
    print(f"Verified repo/list memberships: {verified}/{len(plan['repo_additions'])}")
    print(f"Result saved: {result_path}")
    return 0 if not failures and not mismatches else 4


def main() -> int:
    ap = argparse.ArgumentParser(description="GraphQL-first GitHub native Star Lists sync")
    ap.add_argument("--input-csv", type=Path)
    ap.add_argument("--username")
    ap.add_argument("--repo-col", default="full_name")
    ap.add_argument("--list-col", default="proposed_list")
    ap.add_argument("--confidence-col", default="confidence")
    ap.add_argument("--min-confidence", choices=["Low", "Medium", "High"], default="Low")
    ap.add_argument("--repo-list-mode", choices=["single", "multi"], default="single", help="single keeps one target list per repo; multi allows multiple target lists per repo")
    ap.add_argument("--list-separator", default="", help="Separator for multiple list names in one CSV cell. Default accepts comma, semicolon, or pipe.")
    ap.add_argument("--skip-list", action="append", default=["Review-Inbox-Manual"])
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    ap.add_argument("--limit", type=int, default=0, help="For testing; 0 = all")
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--private", action="store_true", help="Create missing lists as private")
    ap.add_argument("--list-description", default="")
    ap.add_argument("--checkpoint", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan", type=Path)
    ap.add_argument("--confirm-plan-id")
    args = ap.parse_args()

    if args.dry_run and args.apply:
        raise SystemExit("--dry-run and --apply are mutually exclusive")
    if args.dry_run:
        if not args.input_csv:
            raise SystemExit("--dry-run requires --input-csv")
        return dry_run(args)
    if args.apply:
        return apply_plan(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
