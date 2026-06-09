# GitHub Star Lists GraphQL CRUD + Membership Probe (2026-06)

Session-tested against a GitHub account with a temporary Star List that was created, updated, used for repo membership, restored, deleted, and read back. Do not copy token values, device codes, or authorization headers into artifacts.

## Prerequisites

- `gh` CLI authenticated as the target GitHub user.
- OAuth scopes include `user` (plus normal repo/account scopes as needed). If GraphQL returns insufficient scope errors for Star List mutations, refresh with:

```bash
gh auth refresh -h github.com -s user
```

## Verified GraphQL surface

- `createUserList(input:{name,description,isPrivate})`
- `user(login:$login) { lists(first:100) { nodes { id name slug description isPrivate items(...) } } }`
- `updateUserList(input:{listId,name,description,isPrivate})`
- `updateUserListsForItem(input:{itemId,listIds})`
- `deleteUserList(input:{listId})`

## Membership semantics

`updateUserListsForItem` should be treated as a full replacement of the target item's Star List memberships. Safe add/remove pattern:

1. Read the repo/item id and the current list ids for that item.
2. Add mode: `desired = existing_ids ∪ {target_list_id}`.
3. Remove mode: `desired = existing_ids - {target_list_id}`.
4. Call `updateUserListsForItem(input:{itemId, listIds: desired})`.
5. Read back and verify the final list set.

Never send only the new target list id unless intentionally replacing all memberships.

## Tested cleanup behavior

A temporary list was deleted and verified absent after the probe. The test repo's original membership was restored after the add/remove test. For future probes, always include cleanup and read-back verification so the account is not left with throwaway Lists or changed repo organization.

## If GraphQL is unavailable

Do not use browser-cookie or web-fragment write paths. If GraphQL breaks or the user cannot grant the `user` scope, stop and ask the user to configure a suitable GitHub token or `gh` authentication. Treat old `/star_lists` and web form endpoints as obsolete historical context, not implementation options.
