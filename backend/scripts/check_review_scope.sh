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
# commit is about to include. This script prints the four known such shapes
# (staged-then-removed, staged-then-reverted, staged mode-only, staged
# symlink/file type swap) plus the two that are safe (deletion, and a normally
# staged change). A staged RENAME is also safe — verified separately, not here.
#
# Referenced by: backend/skills/s_autonomous-pipeline/stages/deliver.md (Step 1
# scope note).
# Run:  bash backend/scripts/check_review_scope.sh
set -euo pipefail

# ISOLATION FIRST — before any git call. `git init` + `cd` do NOT shield this;
# git's environment beats the cwd. Two distinct leaks, both measured:
#
#   LOCATION (GIT_DIR & friends): with GIT_DIR alone set, this script ran to
#   completion (exit 0), printed the CALLER's tracked files as if they were the
#   fixture, and left `D a.txt` staged in the caller's index. The `git reset
#   --hard` calls below are one step from destroying uncommitted work that way.
#
#   CONFIG (GIT_CONFIG_COUNT / GIT_CONFIG_PARAMETERS): injecting
#   core.fileMode=false made the mode-only scenario print `pending []` while
#   still asserting "gate DENIES" — exit 0, evidence silently WRONG. A
#   core.hooksPath injected the same way even executed a caller hook inside the
#   fixture. A demo whose numbers can be falsified by ambient env is worse than
#   no demo, so this unset is correctness AND data safety, not tidiness.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY \
      GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_CEILING_DIRECTORIES GIT_COMMON_DIR \
      GIT_NAMESPACE GIT_CONFIG GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS \
      GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM GIT_CONFIG_NOSYSTEM GIT_ATTR_NOSYSTEM

TMP="${TMPDIR:-/tmp}/check_review_scope.$$"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP"
cd "$TMP"

git init -q .
# Pin the settings the scenarios depend on, so ambient/global config cannot
# falsify the output. core.fileMode=false makes the mode-only scenario vanish
# (it is the default on some Windows/CIFS checkouts); autocrlf would perturb the
# content comparisons.
git config core.fileMode true
git config core.autocrlf false
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
echo "== 5. staged MODE change, worktree mode reverted (content identical) =="
git reset -q --hard
chmod +x a.txt          # worktree 755
git add a.txt           # index 755
chmod 644 a.txt         # worktree back to 644; content never changed
printf '  %-34s %s\n' "index mode" "$(git ls-files -s a.txt | cut -d' ' -f1)"
show
echo "  -> content-identical mode-only change: covered is EMPTY => gate DENIES."
echo "     Real trigger: chmod +x a script, stage it, then a formatter or"
echo "     checkout resets the bit. NOTE: needs core.fileMode=true (pinned"
echo "     above); with it false this shape does not exist at all."

echo
echo "== 5b. staged symlink -> regular-file swap, reverted on disk =="
git reset -q --hard
echo target > target.txt
ln -s target.txt link.txt
git add target.txt link.txt
git -c user.email=dev@local -c user.name=dev -c commit.gpgsign=false commit -qm links
rm link.txt
echo target > link.txt      # index: REGULAR file, byte-identical content
git add link.txt
rm link.txt
ln -s target.txt link.txt   # worktree back to the HEAD symlink
printf '  %-34s %s\n' "index mode" "$(git ls-files -s link.txt | cut -d' ' -f1)"
show
echo "  -> TYPE-only change: covered is EMPTY => gate DENIES. Unlike scenario 5"
echo "     this one survives core.fileMode=false, so it is the more robust case."

echo
echo "== 6. staged DELETION (fine — appears in both) =="
git reset -q --hard
git rm -q a.txt
show

echo
echo "Conclusion: the reviewer must see the change ON DISK, not merely in the index."
