#!/bin/bash
# check_review_scope.sh — demonstrate, in a throwaway repo, WHY the adversarial
# commit gate can DENY a review that genuinely happened.
#
# The gate compares two independently-computed path sets:
#   covered  = git diff --name-only HEAD      (runtime_hooks._reviewed_paths_at_head,
#                                              captured at SubagentStop)
#   pending  = git diff --name-only --cached  (security_hooks._pending_commit_paths)
# and requires  pending ⊆ covered.
#
# `covered` compares the WORKTREE to HEAD; `pending` compares the INDEX to HEAD.
# Wherever those two disagree, a real review produces no coverage for a file the
# commit is about to include. This script prints every such shape.
#
# Referenced by: skills/s_autonomous-pipeline/stages/deliver.md (Step 1 scope note).
# Run:  bash backend/scripts/check_review_scope.sh
set -euo pipefail

TMP="${TMPDIR:-/tmp}/check_review_scope.$$"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP"
cd "$TMP"

git init -q .
echo tracked > a.txt
git add a.txt
git -c user.email=dev@local -c user.name=dev -c commit.gpgsign=false commit -qm init

show() {
  printf '  %-34s [%s]\n' "covered (diff HEAD)"   "$(git diff --name-only HEAD    | tr '\n' ' ')"
  printf '  %-34s [%s]\n' "pending (diff --cached)" "$(git diff --name-only --cached | tr '\n' ' ')"
}

echo "== 1. modified tracked + brand-new UNTRACKED file =="
echo change >> a.txt
echo brandnew > b.txt
show
printf '  %-34s [%s]\n' "ls-files --others" "$(git ls-files --others --exclude-standard | tr '\n' ' ')"
echo "  -> b.txt is invisible to EVERY diff form; only ls-files finds it."

echo
echo "== 2. after 'git add b.txt' (the fix) =="
git add b.txt
show
echo "  -> b.txt now in BOTH: staging is what makes coverage possible."

echo
echo "== 3. staged-new, then REMOVED from the worktree (staging NOT sufficient) =="
rm b.txt
show
echo "  -> pending has b.txt, covered does not => gate DENIES."

echo
echo "== 4. staged edit, then worktree REVERTED to HEAD content (also not sufficient) =="
git reset -q
echo change >> a.txt
git add a.txt
echo tracked > a.txt
show
echo "  -> pending has a.txt, covered does not => gate DENIES."

echo
echo "== 5. staged DELETION (fine — appears in both) =="
git reset -q --hard
git rm -q a.txt
show

echo
echo "Conclusion: the reviewer must see the change ON DISK, not merely in the index."
