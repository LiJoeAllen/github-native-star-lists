## github-native-star-lists

[English](README.md) | **简体中文**

`github-native-star-lists` 是一个 Codex skill 和本地 CLI 工作流，用于把 GitHub starred repositories 整理到 GitHub 原生 **Star Lists**。

它只通过 `gh` 调用 GitHub GraphQL。不使用浏览器 cookie、CSRF 抓取、Playwright UI 自动化或非官方 web fragment。

## 功能特性

- **前置检查**：Python、`gh`、认证状态、token scopes、GraphQL 可用性。
- **只读导出 starred repos**，包含 `starred_at` 时间。
- **从用户自己的 starred repo metadata 生成 taxonomy 草稿**。
- **基于用户审阅后的 taxonomy JSON 做 metadata-first 分类**。
- **支持 single / multi 模式**：每个 repo 进入一个 List 或多个 Lists。
- **GraphQL dry-run plan**：写入前生成稳定 plan id。
- **默认 additive sync**：只创建缺失 Lists 和添加 memberships，不删除、不移除。
- **checkpoint/resume 和 read-back verification**。
- **中英双语 List 描述更新**，写入后回读验证。

## 目录结构

```text
.
├── SKILL.md                                  # Agent 使用的 skill 指令
├── README.md                                 # 英文说明
├── README.zh-CN.md                           # 中文说明
├── LICENSE                                   # MIT license
├── .gitignore                                # 本地缓存和生成文件忽略规则
├── evals/
│   └── evals.json                            # Skill eval prompts
├── references/
│   ├── graphql-star-list-crud-2026-06.md     # 已验证 GraphQL 行为记录
│   └── list_descriptions_zh_en.json          # 中英双语描述示例
└── scripts/
    ├── check_prereqs.py                      # 环境和认证预检
    ├── discover_taxonomy.py                  # 从 starred metadata 生成 taxonomy 草稿
    ├── classify_stars.py                     # 按 taxonomy 分类 stars
    ├── sync_star_lists_graphql.py            # GraphQL dry-run/apply 同步
    └── update_list_descriptions.py           # 更新 List 描述
```

## 环境要求

- Python 3.10 或更新版本
- GitHub CLI `gh`
- `gh` 已登录目标 GitHub 账号，或 `GH_TOKEN` 可被 `gh` 使用
- GitHub token 需要 OAuth `user` scope

如需刷新 scope：

```bash
gh auth refresh -h github.com -s user
```

## 快速开始

检查本地环境：

```bash
python scripts/check_prereqs.py
```

导出 starred repositories：

```bash
mkdir -p ~/.cache/github-native-star-lists

gh api --paginate \
  -H 'Accept: application/vnd.github.star+json' \
  '/user/starred?per_page=100' \
  > ~/.cache/github-native-star-lists/starred_pages.json
```

从用户自己的 starred repos 生成 taxonomy 草稿：

```bash
python scripts/discover_taxonomy.py \
  --input-json ~/.cache/github-native-star-lists/starred_pages.json
```

审阅并修改：

```text
~/.cache/github-native-star-lists/taxonomy_draft_latest.json
~/.cache/github-native-star-lists/taxonomy_draft_review_latest.md
```

每个 repo 只进入一个目标 List：

```bash
python scripts/classify_stars.py \
  --input-json ~/.cache/github-native-star-lists/starred_pages.json \
  --taxonomy-json ~/.cache/github-native-star-lists/taxonomy_draft_latest.json \
  --repo-list-mode single
```

允许一个 repo 进入多个目标 Lists：

```bash
python scripts/classify_stars.py \
  --input-json ~/.cache/github-native-star-lists/starred_pages.json \
  --taxonomy-json ~/.cache/github-native-star-lists/taxonomy_draft_latest.json \
  --repo-list-mode multi
```

生成 GraphQL dry-run plan：

```bash
python scripts/sync_star_lists_graphql.py \
  --input-csv ~/.cache/github-native-star-lists/stars_lists_with_confidence_latest.csv \
  --repo-list-mode single \
  --dry-run
```

确认 plan id 后才执行写入：

```bash
python scripts/sync_star_lists_graphql.py \
  --apply \
  --plan ~/.cache/github-native-star-lists/graphql_sync_plan_YYYYMMDD_HHMMSS.json \
  --confirm-plan-id PLAN_ID
```

## 更新 List 描述

List 创建后，可以用 JSON 映射批量设置或刷新描述：

```bash
python scripts/update_list_descriptions.py \
  --descriptions-json references/list_descriptions_zh_en.json \
  --dry-run
```

确认后执行并回读验证：

```bash
python scripts/update_list_descriptions.py \
  --descriptions-json references/list_descriptions_zh_en.json
```

JSON 支持两种格式：简单的 `{"List-Name": "描述"}`，或
`{"lists": [{"name": "List-Name", "description": "描述"}]}`。脚本会保留
List 名称和公开/私有设置，写入后回读验证；遇到缺失 List、写入失败或
描述不一致时返回非零退出码。`--username` 可指定其他账号。

## Taxonomy

本 skill 不内置通用固定 taxonomy。用户可以从自己的 starred repo metadata 生成草稿，也可以提供自定义 taxonomy JSON：

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

仅语言规则是弱信号。topic、term 和 phrase 规则通常能提供更高置信度。

## 安全约束

- 只使用 GraphQL：不使用 cookie、CSRF 抓取、Playwright 或非官方 web fragment。
- 写入前必须 dry-run。
- 写入必须提供精确的 `--confirm-plan-id`。
- 默认 sync 是 additive，不删除 Lists，也不移除 memberships。
- `updateUserListsForItem` 会替换某个 repo 的完整 List membership，因此脚本会先读取现有 memberships，并保留不相关的 list ids。

## 测试

运行本地语法检查：

```bash
python -m py_compile scripts/*.py
python -m json.tool evals/evals.json
python -m json.tool references/list_descriptions_zh_en.json
```

使用当前 GitHub 认证做前置检查：

```bash
python scripts/check_prereqs.py
```

## 隐私

不要提交导出的 star 数据、生成的 plans、本地缓存、日志、tokens、authorization headers 或机器相关路径。生成文件建议放在 `~/.cache/github-native-star-lists/` 或其他本地输出目录。

## License

MIT。见 [LICENSE](LICENSE)。
