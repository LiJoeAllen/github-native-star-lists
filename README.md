## github-native-star-lists

**English** | [简体中文](README.zh-CN.md)

`github-native-star-lists` is a Codex skill and local CLI workflow for organizing GitHub starred repositories into GitHub native **Star Lists**.

It uses GitHub GraphQL through `gh` only. It does not use browser cookies, CSRF scraping, Playwright UI automation, or unofficial web fragments.

## Features

- **Preflight checks** for Python, `gh`, authentication, token scopes, and GraphQL access.
- **Read-only starred repo export** with GitHub's star timestamp media type.
- **User-specific taxonomy discovery** from the user's own starred repo metadata.
- **Metadata-first classification** against a reviewed taxonomy JSON.
- **Single-list or multi-list modes** for repo organization.
- **GraphQL dry-run plans** with stable plan ids before any write.
- **Additive sync by default**: create missing lists and add memberships without deleting or removing anything.
- **Checkpoint/resume and read-back verification** for write runs.

## Repository layout

```text
.
├── SKILL.md                                  # Agent-facing skill instructions
├── README.md                                 # English documentation
├── README.zh-CN.md                           # Simplified Chinese documentation
├── LICENSE                                   # MIT license
├── .gitignore                                # Local cache and generated-file ignores
├── evals/
│   └── evals.json                            # Skill eval prompts
├── references/
│   └── graphql-star-list-crud-2026-06.md     # Verified GraphQL behavior notes
└── scripts/
    ├── check_prereqs.py                      # Environment and auth preflight
    ├── discover_taxonomy.py                  # Draft taxonomy from starred metadata
    ├── classify_stars.py                     # Classify stars against taxonomy
    └── sync_star_lists_graphql.py            # Dry-run/apply GraphQL sync
```

## Requirements

- Python 3.10 or newer
- GitHub CLI `gh`
- `gh` authenticated to the target GitHub account, or `GH_TOKEN` available to `gh`
- A GitHub token with the OAuth `user` scope

Refresh scopes when needed:

```bash
gh auth refresh -h github.com -s user
```

## Quick start

Check the local environment:

```bash
python scripts/check_prereqs.py
```

Export starred repositories:

```bash
mkdir -p ~/.cache/github-native-star-lists

gh api --paginate \
  -H 'Accept: application/vnd.github.star+json' \
  '/user/starred?per_page=100' \
  > ~/.cache/github-native-star-lists/starred_pages.json
```

Generate a draft taxonomy from the user's own starred repos:

```bash
python scripts/discover_taxonomy.py \
  --input-json ~/.cache/github-native-star-lists/starred_pages.json
```

Review and edit:

```text
~/.cache/github-native-star-lists/taxonomy_draft_latest.json
~/.cache/github-native-star-lists/taxonomy_draft_review_latest.md
```

Classify into one target list per repo:

```bash
python scripts/classify_stars.py \
  --input-json ~/.cache/github-native-star-lists/starred_pages.json \
  --taxonomy-json ~/.cache/github-native-star-lists/taxonomy_draft_latest.json \
  --repo-list-mode single
```

Or allow multiple target lists per repo:

```bash
python scripts/classify_stars.py \
  --input-json ~/.cache/github-native-star-lists/starred_pages.json \
  --taxonomy-json ~/.cache/github-native-star-lists/taxonomy_draft_latest.json \
  --repo-list-mode multi
```

Create a GraphQL dry-run plan:

```bash
python scripts/sync_star_lists_graphql.py \
  --input-csv ~/.cache/github-native-star-lists/stars_lists_with_confidence_latest.csv \
  --repo-list-mode single \
  --dry-run
```

Apply only after reviewing and approving the exact plan id:

```bash
python scripts/sync_star_lists_graphql.py \
  --apply \
  --plan ~/.cache/github-native-star-lists/graphql_sync_plan_YYYYMMDD_HHMMSS.json \
  --confirm-plan-id PLAN_ID
```

## Taxonomy

This skill does not ship a universal fixed taxonomy. Each user can discover a draft from their own starred repo metadata or provide a custom taxonomy JSON:

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

Language-only rules are weak signals. Topic, term, and phrase rules provide better classification confidence.

## Safety

- GraphQL only: no cookies, CSRF scraping, Playwright, or unofficial web fragments.
- Dry-run before writes.
- Writes require an exact `--confirm-plan-id`.
- Default sync is additive and does not delete lists or remove memberships.
- `updateUserListsForItem` replaces the full membership set for a repo, so the script reads existing memberships and preserves unaffected list ids.

## Testing

Run local syntax checks:

```bash
python -m py_compile scripts/*.py
python -m json.tool evals/evals.json
```

Run the preflight check against the current GitHub auth:

```bash
python scripts/check_prereqs.py
```

## Privacy

Do not commit exported star data, generated plans, local caches, logs, tokens, authorization headers, or machine-specific paths. Generated files should stay under `~/.cache/github-native-star-lists/` or another local output directory.

## License

MIT. See [LICENSE](LICENSE).
