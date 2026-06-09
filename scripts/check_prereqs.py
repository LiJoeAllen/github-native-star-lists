#!/usr/bin/env python3
"""Check prerequisites for github-native-star-lists."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from typing import Any


def run(cmd: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def parse_scopes(text: str) -> list[str]:
    match = re.search(r"Token scopes:\s*(.+)", text)
    if not match:
        return []
    return re.findall(r"'([^']+)'", match.group(1))


def gh_graphql(query: str, variables: dict[str, Any]) -> tuple[bool, str]:
    payload = json.dumps({"query": query, "variables": variables})
    proc = run(["gh", "api", "graphql", "--input", "-"], input_text=payload)
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        return False, output.strip()
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return False, "gh api graphql returned non-JSON output"
    if data.get("errors"):
        return False, json.dumps(data["errors"], ensure_ascii=False)
    return True, "ok"


def main() -> int:
    ap = argparse.ArgumentParser(description="Check prerequisites for GitHub native Star Lists GraphQL workflow")
    ap.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = ap.parse_args()

    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    py_ok = sys.version_info >= (3, 10)
    add("python>=3.10", py_ok, sys.version.split()[0])

    gh_path = shutil.which("gh")
    add("gh-installed", bool(gh_path), gh_path or "gh not found in PATH")

    login = ""
    scopes: list[str] = []
    if gh_path:
        auth = run(["gh", "auth", "status"])
        auth_text = (auth.stdout or "") + (auth.stderr or "")
        add("gh-auth-status", auth.returncode == 0, "authenticated" if auth.returncode == 0 else auth_text.strip())
        scopes = parse_scopes(auth_text)
        add("gh-user-scope", "user" in scopes, ", ".join(scopes) if scopes else "could not read token scopes")

        user = run(["gh", "api", "user", "--jq", ".login"])
        login = user.stdout.strip()
        add("gh-api-user", user.returncode == 0 and bool(login), login or ((user.stderr or user.stdout).strip()))

        if login:
            query = """
            query($login: String!) {
              user(login: $login) {
                login
                lists(first: 1) { nodes { id name } }
              }
            }
            """
            ok, detail = gh_graphql(query, {"login": login})
            if not ok and ("INSUFFICIENT_SCOPES" in detail or "insufficient" in detail.lower()):
                detail += " | Run: gh auth refresh -h github.com -s user"
            add("graphql-user-lists-read", ok, detail)
        else:
            add("graphql-user-lists-read", False, "skipped because GitHub login could not be inferred")
    else:
        add("gh-auth-status", False, "skipped because gh is missing")
        add("gh-user-scope", False, "skipped because gh is missing")
        add("gh-api-user", False, "skipped because gh is missing")
        add("graphql-user-lists-read", False, "skipped because gh is missing")

    ok = all(item["ok"] for item in checks)

    if args.json:
        print(json.dumps({"ok": ok, "login": login, "checks": checks}, ensure_ascii=False, indent=2))
    else:
        print("GitHub native Star Lists prerequisite check")
        if login:
            print(f"Account: {login}")
        for item in checks:
            mark = "OK" if item["ok"] else "FAIL"
            print(f"- {mark}: {item['name']} - {item['detail']}")
        if not ok:
            print("\nFix failed checks before running export, classification, dry-run, or apply.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
