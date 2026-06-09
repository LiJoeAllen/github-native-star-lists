---
name: github-native-star-lists
description: Organize GitHub starred repositories into native Star Lists via GraphQL. Use for 整理/分类 GitHub stars, taxonomy discovery, single/multi-list classification, dry-run sync, Star List writes, checkpoint/resume, and read-back verification. Requires gh or GH_TOKEN with user scope; never use cookies, CSRF, Playwright, or web fragments.
version: 1.3.0
author: github-native-star-lists contributors
license: MIT
platforms: [macos, linux]
metadata:
  tags: [GitHub, Starred Repositories, Star Lists, GraphQL, automation, classification]
  related_skills: [github-auth, github-repo-management]
---

# GitHub Native Star Lists Organizer

Use this skill to classify GitHub starred repositories and safely sync the result into GitHub native Star Lists through GitHub GraphQL.

This is a public skill. Do not assume that all users want the same List names. Build or load a taxonomy from the user's own stars and preferences before classification.

Hard boundary: use GraphQL only. Do not use browser cookies, CSRF scraping, Playwright UI automation, or unofficial web fragment endpoints for Star List writes. If GraphQL access is unavailable, stop and ask the user to configure the right GitHub token or `gh` auth scope.

## Prerequisites

The user must have:

- Python 3.10 or newer.
- GitHub CLI `gh` installed.
- `gh` authenticated to the target GitHub account, or `GH_TOKEN` configured for `gh`.
- A token that can call native Star List GraphQL APIs. The verified requirement is the OAuth `user` scope.

Recommended setup:

```bash
gh auth refresh -h github.com -s user
gh auth status
```

Alternatively, the user can provide a GitHub token through the normal `gh` environment, such as `GH_TOKEN`, if it has the required scope. Never ask for a token to be pasted into chat unless there is no safer local credential path; prefer `gh auth login`, `gh auth refresh`, or an environment variable.

If GraphQL returns `INSUFFICIENT_SCOPES`, pause and ask the user to refresh or replace the token. Do not fall back to cookies.

Before any export, classification, dry-run, or write, run the preflight check:

```bash
python scripts/check_prereqs.py
```

It checks Python, `gh`, authentication, visible token scopes, and a read-only GraphQL `User.lists` probe. Use `--json` for machine-readable output in automation.

## Safety Model

Star List writes mutate the user's GitHub account. Separate the work into clear phases:

1. Read-only export: no confirmation needed if GitHub auth is already available.
2. Classification and review artifacts: no GitHub writes.
3. Dry-run sync plan: read current native Lists and show exactly what would change.
4. Write phase: only after explicit user approval with the exact plan id.
5. Verification: read GitHub back and confirm each planned list and repo/list membership reached the expected state.

Default sync mode is additive: create missing lists and add missing repo memberships. Never delete existing lists, rename lists, or remove repos from lists unless the user explicitly asks for an exact/reset/destructive mode and a separate implementation supports it.

GraphQL `updateUserListsForItem(input:{itemId,listIds})` treats `listIds` as the complete desired membership set for that repo. Preserve unaffected memberships by reading existing memberships first, then unioning the target list ids into the existing set.

## Organization Mode

Ask or infer whether the user wants each repository in one list or multiple lists:

- `single`: one target List per repo. Use this for clean primary-category organization. If a CSV row has multiple list names, keep the first and skip the rest with an audit reason.
- `multi`: multiple target Lists per repo. Use this when tags overlap, for example a repo can be both `Topic-Name` and `Language-Python`.

Both modes are additive during sync. `single` controls the target classification plan; it does not remove existing GitHub List memberships.

In CSV artifacts, put the target list names in `proposed_list`. For `multi`, a cell may contain multiple names separated by comma, semicolon, or pipe, or the CSV may include multiple rows for the same repo.

Use the same mode for classification and sync. The mode is included in the sync plan id.

## Taxonomy Model

There is no universal fixed List vocabulary. Before classification, choose one taxonomy source:

- Existing user preference: a user-provided taxonomy JSON.
- Discovery draft: run `scripts/discover_taxonomy.py` against the user's exported stars, then let the user review, rename, merge, split, or delete entries.
- Existing GitHub Lists: if the user already has native Star Lists, use those names as the target vocabulary and build/edit a matching taxonomy JSON.

Taxonomy JSON schema:

```json
{
  "version": 1,
  "lists": [
    {
      "name": "List-Name",
      "topics": ["github-topic"],
      "terms": ["repo-name-or-description-token"],
      "phrases": ["description phrase"],
      "languages": ["Python"]
    }
  ]
}
```

Treat language-only rules as weak. Prefer topic, term, and phrase rules for high-confidence classification.

## Phase 1 - Preflight And Export Starred Repos

Run preflight first:

```bash
python scripts/check_prereqs.py
```

Prefer `gh` because it uses local GitHub auth and avoids handling tokens directly.

```bash
mkdir -p ~/.cache/github-native-star-lists

gh api --paginate \
  -H 'Accept: application/vnd.github.star+json' \
  '/user/starred?per_page=100' \
  > ~/.cache/github-native-star-lists/starred_pages.json
```

`gh api --paginate` may emit multiple JSON arrays concatenated together. Parse robustly with `json.JSONDecoder().raw_decode()` in a loop. With the star media type, each row is usually `{starred_at, repo}`; normalize with:

```python
repo = item.get("repo", item)
```

Keep these fields for classification and auditability:

- `starred_at`
- `full_name`, `html_url`
- `description`, `topics`, `language`
- `stargazers_count`, `archived`, `pushed_at`, `default_branch`

Recommended output path:

```text
~/.cache/github-native-star-lists/stars_latest.json
```

## Phase 2 - Discover Or Load Taxonomy

Generate a draft taxonomy from the user's own stars when they do not provide one:

```bash
python scripts/discover_taxonomy.py \
  --input-json ~/.cache/github-native-star-lists/starred_pages.json
```

Default outputs:

```text
~/.cache/github-native-star-lists/taxonomy_draft_latest.json
~/.cache/github-native-star-lists/taxonomy_draft_review_latest.md
```

Review the draft with the user before classification. The discovery script intentionally produces a draft, not a final answer.

## Phase 3 - Metadata-First Classification

Classify every repo from cheap metadata against the reviewed taxonomy JSON.

For each row, produce:

- `full_name`
- `proposed_list`
- `confidence`: `High`, `Medium`, or `Low`
- `confidence_score`: numeric 0-1 if useful
- `reason`: concise evidence, such as topic, description, or name tokens
- source fields needed for audit: `description`, `topics`, `language`, `starred_at`

When using `multi`, `proposed_list` can hold multiple list names separated by `|`.

Confidence rubric:

- High: exact topic/name token or explicit description phrase strongly supports the list.
- Medium: plausible but generic evidence, second-pass heuristic, or multiple weak signals.
- Low: sparse metadata, no description/topics, generic language-only evidence, category collision, or suspicious/disguised metadata.

Do not let programming language dominate classification. Language is a nudge, not the category.

Use tokenized matching. Avoid naive substring false positives:

- `daily`, `aims`, and `maintain` are not `AI`.
- `Star Rail` is not automatically `AI`.
- generic `Go`, `Python`, or `TypeScript` should not decide a category alone.

User corrections override metadata. Add user-specific corrections to the taxonomy JSON rather than hard-coding them in the public skill.

Use `scripts/classify_stars.py` for the first pass:

```bash
# One primary target List per repo.
python scripts/classify_stars.py \
  --input-json ~/.cache/github-native-star-lists/starred_pages.json \
  --taxonomy-json ~/.cache/github-native-star-lists/taxonomy_draft_latest.json \
  --repo-list-mode single

# Allow multiple target Lists per repo.
python scripts/classify_stars.py \
  --input-json ~/.cache/github-native-star-lists/starred_pages.json \
  --taxonomy-json ~/.cache/github-native-star-lists/taxonomy_draft_latest.json \
  --repo-list-mode multi
```

The classifier uses only metadata and the reviewed taxonomy: repo name, owner/name, description, topics, language, stars, archived state, pushed date, and starred date. It tokenizes names/descriptions to avoid naive substring matches. Language is only a weak signal; it cannot by itself create a high-confidence classification.

Default outputs:

```text
~/.cache/github-native-star-lists/stars_lists_with_confidence_latest.csv
~/.cache/github-native-star-lists/low_confidence_review_latest.csv
~/.cache/github-native-star-lists/classification_summary_latest.md
```

The output CSV is directly usable by `scripts/sync_star_lists_graphql.py`.

## Phase 4 - Escalate Uncertain Repos

Escalate low-confidence repos in increasing cost order:

1. Fetch README only for `Low` rows.
2. If still unclear, inspect top-level files or repo contents.
3. Use web search only for the remaining small manual bucket.
4. If still ambiguous, keep `Review-Inbox-Manual` rather than forcing a bad list.

Recommended artifacts:

```text
stars_lists_with_confidence_latest.csv
low_confidence_review_latest.csv
readme_reclass_changes_latest.csv
manual_web_research_latest.csv
classification_summary_latest.md
```

Before writing, present a compact review:

- total stars
- repo-list mode: `single` or `multi`
- counts by proposed list
- confidence distribution
- manual/ambiguous repos
- notable corrections or category collisions
- exact write scope recommendation, such as "write all High+Medium, skip Review-Inbox-Manual"

## Phase 5 - GraphQL Sync

Use `scripts/sync_star_lists_graphql.py` for sync. It reads GitHub state through `gh`, writes a plan JSON, requires a matching plan id before `--apply`, checkpoints mutations, and verifies by reading list memberships back.

```bash
# Dry-run: one target list per repo.
python scripts/sync_star_lists_graphql.py \
  --input-csv ~/.cache/github-native-star-lists/stars_lists_with_confidence_latest.csv \
  --repo-list-mode single \
  --dry-run

# Dry-run: allow multiple target lists per repo.
python scripts/sync_star_lists_graphql.py \
  --input-csv ~/.cache/github-native-star-lists/stars_lists_with_confidence_latest.csv \
  --repo-list-mode multi \
  --dry-run

# Apply: only after the user approves the exact plan.
python scripts/sync_star_lists_graphql.py \
  --apply \
  --plan ~/.cache/github-native-star-lists/graphql_sync_plan_YYYYMMDD_HHMMSS.json \
  --confirm-plan-id PLAN_ID
```

The script infers the account from `gh api user --jq .login` unless `--username` is supplied.

Verified GraphQL surface, tested on 2026-06-06:

- `createUserList(input:{name,description,isPrivate})`
- `user(login){ lists(first:100){ nodes { id name slug description isPrivate items(...) }}}`
- `updateUserList(input:{listId,name,description,isPrivate})`
- `updateUserListsForItem(input:{itemId,listIds})`
- `deleteUserList(input:{listId})`

See `references/graphql-star-list-crud-2026-06.md` for the verified GraphQL probe and membership semantics.

### Dry-Run Output

Use this structure when reporting a plan:

```markdown
## GitHub Star Lists dry-run
Account: USERNAME
Source: stars_lists_with_confidence_latest.csv
Mode: additive
Repo list mode: single | multi
Plan id: PLAN_ID

### Summary
- Lists to create: N
- Repos to add: N
- Already assigned: N
- Skipped: Review-Inbox-Manual / Low confidence rows / single-mode extras

### Planned changes
- Topic-Name: create, add 42 repos
- Language-Python: already exists, add 98 repos

### Approval needed
Reply with the plan id and scope to proceed.
```

### Write Verification Output

After execution, report:

```markdown
## GitHub Star Lists sync result
- Created lists: X
- Added repo/list memberships: Y
- Already correct: Z
- Failed operations: N
- Verified repo/list memberships: A/B
- Mismatches: list names + missing repo counts
- Artifacts: paths to plan/result/checkpoint JSON or JSONL
```

## Final Reminders

- Require a GitHub token or `gh` auth with `user` scope before GraphQL writes.
- Do not use browser cookies, CSRF scraping, Playwright, or unofficial web endpoints.
- Separate classification from writing.
- Ask or infer `single` versus `multi` repo-list mode before the dry-run.
- Avoid spending tokens on every README; escalate only uncertain repos.
- Keep ambiguous repos in `Review-Inbox-Manual`.
- Dry-run first, ask for approval, apply using the confirmed plan id, then read-back verify.
