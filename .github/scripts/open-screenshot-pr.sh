#!/usr/bin/env bash
# Called only after a successful capture from the default branch.
set -euo pipefail
: "${GH_TOKEN:?}" "${GITHUB_REPOSITORY:?}" "${GITHUB_RUN_ID:?}"
: "${GITHUB_SHA:?}" "${SCREENSHOT_PATHS:?}" "${CHECK_WORKFLOWS:?}"
[[ "$GITHUB_REPOSITORY" == TheLogsAreScreaming/* ]] || exit 1
[[ "${GITHUB_REF:-}" == refs/heads/main ]] || exit 1
[[ "${GITHUB_EVENT_NAME:-}" == push || "${GITHUB_EVENT_NAME:-}" == workflow_dispatch ]] || exit 1

# Keep one pending screenshot PR; never overwrite a branch or push main.
existing=$(gh pr list --repo "$GITHUB_REPOSITORY" --base main --state open --json headRefName,author,number --jq '.[] | select(.author.login == "app/github-actions" or .author.login == "github-actions[bot]") | select(.headRefName | startswith("automation/screenshots-")) | .number' | head -n 1)
if [[ -n "$existing" ]]; then
  echo "Screenshot PR #$existing is awaiting review; leaving it unchanged."
  exit 0
fi

git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
while IFS= read -r path; do
  [[ -n "$path" ]] || continue
  [[ "$path" == *.png && "$path" != /* && "$path" != *..* ]] || exit 1
  git add -- "$path"
done <<< "$SCREENSHOT_PATHS"
if git diff --cached --quiet; then
  echo 'Screenshots already up to date.'
  exit 0
fi
# Refuse any staged source changes, including changes from capture tooling.
while IFS= read -r path; do
  grep -Fxq -- "$path" <<< "$SCREENSHOT_PATHS" || { echo "Unexpected staged file: $path"; exit 1; }
done < <(git diff --cached --name-only)
branch="automation/screenshots-${GITHUB_RUN_ID}"
git switch -c "$branch"
git commit -m 'docs: refresh demo screenshots'
git push origin "HEAD:refs/heads/$branch"
gh pr create --repo "$GITHUB_REPOSITORY" --base main --head "$branch" \
  --title 'docs: refresh demo screenshots' \
  --body 'Automated screenshot refresh from the default branch. Review the images and merge only after the required checks pass. This workflow does not approve or merge the pull request.'
# GITHUB_TOKEN-created PR runs can need approval. Dispatch checks explicitly
# on the screenshot commit so branch protection can receive their results.
while IFS= read -r workflow; do
  [[ -n "$workflow" ]] || continue
  gh workflow run "$workflow" --repo "$GITHUB_REPOSITORY" --ref "$branch"
done <<< "$CHECK_WORKFLOWS"
