# Git/GitHub Claude Attribution Audit — Safe Cleanup Plan

Run this with **Claude Code** (or manually) inside your actual repository. It has direct filesystem/git access; this chat does not.

IMPORTANT REQUIREMENTS:

1. DO NOT delete, modify, revert, or regenerate any project source code.
2. DO NOT remove any files created by Claude.
3. DO NOT remove Claude-generated comments inside source files.
4. DO NOT change application functionality.
5. DO NOT delete commit messages.
6. DO NOT delete legitimate project history except where Git metadata must be rewritten to remove Claude attribution.
7. DO NOT push anything to GitHub until all local verification checks pass.
8. NEVER use `git reset --hard`, `git clean -fd`, or any destructive command that could remove working files.
9. Preserve all existing branches and create a safety backup before modifying history.
10. The goal is ONLY to remove Claude/Anthropic attribution from Git metadata and prevent future Claude co-author attribution.

## REPOSITORY GOAL

- All Claude-generated code preserved.
- All Claude-generated comments preserved.
- All files preserved.
- All project functionality preserved.
- Existing commit messages preserved.
- GitHub identity remains the author/committer.
- Claude/Anthropic does NOT appear as a Git co-author.
- Claude/Anthropic does NOT appear in GitHub Contributors after recalculation.
- Future commits do NOT contain a Claude/Anthropic Co-Authored-By trailer.

## STEP 1 — AUDIT (read-only)

```
git status
git branch -a
git remote -v
git log --all --format="%h | AUTHOR=%an <%ae> | COMMITTER=%cn <%ce> | %s"
git log --all --format="%B"
git config --local --list
git config --global --list
```

Search for: `Claude`, `claude`, `Anthropic`, `anthropic`, `Co-Authored-By`, `Co-authored-by`, `noreply@anthropic.com`.
Do NOT modify anything in this step.

## STEP 2 — SAFETY BACKUPS

Create backup refs for every local branch before touching history. Do not delete existing backups. Record current HEAD of every local branch.

If there are uncommitted changes: **STOP** and report them. Do not stash/reset/delete automatically.

## STEP 3 — IDENTIFY EXACT ATTRIBUTION

Determine whether Claude/Anthropic appears as:
A. commit author
B. commit committer
C. Co-Authored-By trailer
D. GitHub collaborator/access entry
E. remote/automation identity
F. Git config that causes future co-author trailers

Do NOT remove anything just because the word "Claude" appears in a normal commit message (e.g. "from claude we are try to do things again" stays untouched). Only actual attribution metadata is removed.

## STEP 4 — REMOVE CO-AUTHOR METADATA

If commits contain `Co-Authored-By: Claude ...` / `Co-authored-by: Claude Sonnet 5 <noreply@anthropic.com>` etc., remove ONLY those trailer lines. Do not touch source files or the rest of the commit message.

If Claude is an actual commit **author** (not just co-author trailer): **STOP** and explain exactly which commits are affected before changing anything.

## STEP 5 — VERIFY CONTENT INTEGRITY

After metadata cleanup, verify against the Step 2 backups:
- source files still exist, none deleted
- no uncommitted work lost
- no Claude-generated code or comments removed
- branch names intact
- commit messages intact except the removed trailer
- author/committer identity correct

The expected diff vs backups should be commit metadata/hashes only — never source file deletions.

## STEP 6 — PREVENT FUTURE ATTRIBUTION

Find what's causing `Co-Authored-By: Claude ...` to be auto-inserted (Claude Code config, repo-local git hooks/config, etc.) and disable it — without changing git username/email, GitHub auth, or removing Claude Code itself. Verify after changing.

## STEP 7 — FINAL METADATA AUDIT

```
git log --all --format="%B" | grep -Ei "Co-Authored-By.*Claude|Co-authored-by.*Claude|Co-Authored-By.*Anthropic|Co-authored-by.*Anthropic|noreply@anthropic.com"
```
Must return nothing. Then re-run the Step 1 author/committer log — Claude may still appear in ordinary commit message text; that's fine.

## STEP 8 — GITHUB ACCESS CHECK

Check whether Claude/Anthropic has actual repo access via Collaborators, Teams, GitHub Apps, or repo permissions. Do NOT auto-remove any legitimate human collaborator or integration. If Claude/Anthropic is found, report exactly where and what permission level, and only explain (don't execute) a safe removal if one exists.

## STEP 8.5 — PRE-PUSH HARD-CODED VALUE CHECK  *(new)*

Before producing the final push-readiness report, scan the diffs on every branch slated for push for hard-coded values that shouldn't ship: API keys/tokens/secrets, absolute local file paths, personal emails/usernames, internal URLs, debug flags, or placeholder credentials.

- Do NOT edit or remove any of these automatically.
- List each finding (file, line, branch).
- Ask the user to confirm or update them.
- Only proceed to Step 9's report once the user has responded (fixed, confirmed safe, or explicitly says to proceed anyway).
- If nothing is found, state that explicitly and proceed.

## STEP 8.6 — HISTORICAL HARD-CODED VALUE CLEANUP IN SOURCE (opt-in, separate from attribution work)  *(new)*

This step modifies actual source code, which is different from every other step above (those touch only Git metadata). Treat it as a distinct, explicitly-approved task, not something bundled silently into the attribution cleanup.

1. **Scan the current working tree** (not just the pending diff) for hard-coded values likely left over from past work/commits — API keys, tokens, secrets, passwords, absolute local file paths, personal emails/usernames, internal-only URLs, hard-coded IPs/ports, debug flags left on, and placeholder/test credentials. Include values sitting in comments, not just active code.
2. For each finding, report: file, line number, the value (redact the secret itself if sensitive, e.g. `sk-••••1234`), and which commit(s) introduced it (`git log -S"<value>" -- <file>`).
3. Do NOT edit anything yet. Present the full list and, for each item, ask whether to:
   - replace it with an environment variable / config reference,
   - remove it entirely,
   - leave it as-is (e.g. it's a non-sensitive example/test value), or
   - handle it some other way you specify.
4. Only after you approve an item does Claude Code make that specific edit — one item at a time or in an approved batch, never a blanket "clean up all hardcoded stuff" pass.
5. After edits, run the project's existing tests/build (if any) to confirm functionality wasn't broken, and show a diff of exactly what changed.
6. Note: fixing the *current* file content does not erase the old value from Git *history* — it will still exist in old commits/blobs unless those commits are also rewritten (which reopens the history-rewrite risks from Steps 2–7). If you want the value scrubbed from history too, say so explicitly and treat it with the same backup/verify rigor as the attribution rewrite — do not combine it silently with that rewrite.
7. If a "finding" turns out to be inside a Claude-generated comment or file, do not delete the surrounding comment/file just to remove the value — edit only the specific hard-coded literal, preserving the rest of the comment/code per rule #2/#3 above.

## STEP 9 — DO NOT PUSH YET

After all local verification (including 8.5) passes: **STOP**. Do NOT run `git push`, `git push --force`, or `git push --force-with-lease`.

Report instead:
1. Number of commits with Claude attribution before cleanup.
2. Number after cleanup.
3. Which branches were rewritten.
4. Whether source files changed.
5. Whether any files were deleted.
6. Whether Claude remains in author/committer metadata.
7. Whether Claude remains in Co-Authored-By trailers.
8. What configuration was changed to prevent future attribution.
9. Result of the Step 8.5 pending-diff hard-coded value scan and how it was resolved.
10. Result of the Step 8.6 historical source-code hard-coded value cleanup (what was found, what you approved, what was changed, test/build results) — or "not run" if you chose to skip it.
11. Whether GitHub remote is configured.
12. Exact commands required to push the cleaned branches.

Wait for explicit user review/approval before pushing.

## SAFETY PRIORITY

If there is ANY uncertainty about whether a command could delete project content, stop and ask rather than executing it.

**Objective:** keep all code + keep all files + keep all Claude-generated work, remove only Claude/Anthropic Git attribution, prevent future Claude co-author trailers, and never push without a clean hard-coded-value check and explicit approval.
