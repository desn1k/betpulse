# Repository instructions

Read `HANDOFF.md` section "0. Current state" first; read other sections only when the task
needs them. This file holds only what must hold in every session; everything else lives in
`HANDOFF.md`.

## Language

- The owner writes in English. Plans, progress updates, blockers and reports to the owner
  are in Russian: plain, concise, tables where useful, no jargon.
- Code, comments, commit messages, PR texts, docs, identifiers and implementation prompts
  are in English.

## Working rules

1. **Plan first.** Show the plan and wait for the owner's "go" before opening a branch or
   writing code.
2. **One branch per task, one open PR at a time.** The next branch starts only after the
   previous PR is merged and the merge is verified (rule 7).
3. **The owner merges.** Never push to `main`, never force-push, never close or reopen a PR
   or delete a branch without the owner's explicit say.
4. **Failing test first.** Every fix starts with a test that fails for the right reason;
   show the red output before the fix, then the green one. Every commit passes mypy and tsc
   on its own.
5. **No AI or model attribution** anywhere that is pushed: commits (no co-author or
   "generated with" lines), PR texts, code, comments, docs.
6. **Stop and report on anything unexpected.** Give options; do not fix it on your own. If a
   tool or classifier refuses a command, do not work around it: give the owner the single
   command to run and wait.
7. **Verify a merge yourself before any post-merge step** (deleting a branch, syncing
   `main`, starting the next task): `gh pr view <n>` shows `MERGED` and `origin/main`
   contains the merge commit. A message saying "merged" is not evidence. Git's "not yet
   merged" warning is a stop signal; never delete an unmerged branch (deleting a PR's head
   branch closes the PR).
8. **Before the owner merges, report three shas:** the PR head, the branch head (local and
   origin), and the sha the green CI and Security runs were made on.
9. **PR completion.** Before calling a PR ready: read every top-level comment, inline
   comment and review thread (outside-diff comments too); re-check for comments added after
   the last commit; address each actionable one or say why it stays open; resolve a thread
   only after replying; verify the required CI and Security checks. Green CI alone is not
   enough.
10. **Releases** always use an explicit `IMAGE_TAG` (never `latest`); the owner runs them
    unless the owner says otherwise for a given run.

## Environment

Details in `HANDOFF.md` §7 (development environment) and §5 (CI, e2e, package-lock).

- Windows 11 with **Git Bash** and PowerShell, **Docker Desktop**. Worktrees and
  `node_modules` junctions stay on the repository's drive (a cross-drive junction breaks
  the Next build).
- Git Bash path mangling: `MSYS2_ARG_CONV_EXCL=/dev/null` for `scripts/deploy.sh` and
  `scripts/rollback.sh`; `MSYS2_ARG_CONV_EXCL='*'` for `docker exec` with paths inside a
  container. With the `/dev/null` setting Windows curl cannot write to `/dev/null`: use
  `curl -o NUL`. Local Caddy TLS: `curl --ssl-no-revoke --cacert caddy-root.crt`.
- `python3` on the host is the Microsoft Store stub: run Python in containers.
- **Backend tests run in a Linux container**, never on the host, with the env of the CI
  backend job and the whole repository mounted. Build the test schema with Alembic
  (`upgrade head`), as CI does; a stale `create_all` schema hides migration gaps.
- Tools not installed on Windows run in Docker: shellcheck, trivy, `node:22`, Linux pytest.
- **`frontend/package-lock.json`: minimal hunks only** (version, resolved, integrity from
  `npm view`), validated with `npm ci` + `npm ls <pkg>` + `npm audit --omit=dev` in a
  `node:22` container on a copy; never `npm install` on the host into the lock.
- Rehearsals run on a separate scratch clone under compose project `betpulse`; do not
  change that stack, its volumes or its `.release/` state unless the owner asks for that
  run.
