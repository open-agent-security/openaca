#!/usr/bin/env bash
# List, reply to, or resolve pull-request review threads.
#
# This exists so the @claude bot can close out the threads it actually
# fixed without being granted `Bash(gh api graphql:*)`. That allowlist
# entry would be arbitrary authenticated GraphQL under a write token —
# mergePullRequest, addPullRequestReview, deleteRef — reachable from a
# prompt injection in the PR content the bot reads. Allowlisting this
# script instead caps the blast radius at "wrong thread resolved", which
# is visible on the PR and reversible by hand.
#
# Usage:
#   review-threads.sh list    <pr>
#   review-threads.sh reply   <pr> <thread-id> <body>
#   review-threads.sh resolve <pr> <thread-id>
# shellcheck disable=SC2016  # GraphQL $variables must reach the API unexpanded
set -euo pipefail

die() { echo "$*" >&2; exit 2; }

CMD="${1:-}"
PR="${2:-}"
[[ "$PR" =~ ^[0-9]+$ ]] || die "usage: $0 {list|reply|resolve} <pr> [thread-id] [body]"

# The <pr> argument is caller-controlled — a prompt injection in content this
# run reads (a PR body, a file, a linter message) could pass a different
# PR number to widen this script's reach beyond the PR actually being worked.
# GITHUB_EVENT_PATH is populated by the Actions runner from the real webhook
# payload before the job starts; nothing in the checkout or in comment/file
# content can forge it. Require the argument to match that trusted number.
TRUSTED_PR=""
if [ -n "${GITHUB_EVENT_PATH:-}" ] && [ -r "$GITHUB_EVENT_PATH" ]; then
  TRUSTED_PR=$(jq -r '.pull_request.number // .issue.number // empty' "$GITHUB_EVENT_PATH")
fi
[[ "$TRUSTED_PR" =~ ^[0-9]+$ ]] || die "no trusted PR number in the triggering event; refusing"
[ "$PR" = "$TRUSTED_PR" ] || die "pr argument ($PR) does not match the triggering PR (#$TRUSTED_PR); refusing"

REPO="${GITHUB_REPOSITORY:-}"
[ -n "$REPO" ] || REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
OWNER="${REPO%%/*}"
NAME="${REPO##*/}"

Q='query($o:String!,$n:String!,$p:Int!,$c:String){repository(owner:$o,name:$n){pullRequest(number:$p){reviewThreads(first:100,after:$c){pageInfo{hasNextPage endCursor}nodes{id isResolved path line comments(first:1){nodes{body}}}}}}}'

# id <TAB> isResolved <TAB> path:line <TAB> first line of the finding
fetch_threads() {
  local cursor="" page
  while :; do
    if [ -n "$cursor" ]; then
      page=$(gh api graphql -f query="$Q" -F o="$OWNER" -F n="$NAME" -F p="$PR" -F c="$cursor")
    else
      page=$(gh api graphql -f query="$Q" -F o="$OWNER" -F n="$NAME" -F p="$PR")
    fi
    jq -r '.data.repository.pullRequest.reviewThreads.nodes[]
           | [ .id,
               (.isResolved | tostring),
               ((.path // "-") + ":" + ((.line // 0) | tostring)),
               ((.comments.nodes[0].body // "") | gsub("[[:space:]]+"; " ") | .[0:140]) ]
           | @tsv' <<<"$page"
    [ "$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage' <<<"$page")" = "true" ] || break
    cursor=$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor' <<<"$page")
  done
}

# A thread id from another PR (or another repo) must not be reachable here:
# the whole point of the script is that its authority is narrower than the
# token's.
assert_thread_on_pr() {
  fetch_threads | cut -f1 | grep -qxF "$1" || die "thread $1 is not on PR #$PR"
}

case "$CMD" in
  list)
    fetch_threads | awk -F'\t' '$2 == "false" { print $1 "\t" $3 "\t" $4 }'
    ;;
  reply)
    TID="${3:?thread-id required}"; BODY="${4:?body required}"
    [[ "$TID" =~ ^[A-Za-z0-9_=-]+$ ]] || die "bad thread id: $TID"
    assert_thread_on_pr "$TID"
    gh api graphql -f query='mutation($t:ID!,$b:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$t,body:$b}){comment{url}}}' \
      -F t="$TID" -F b="$BODY" --jq '"replied " + .data.addPullRequestReviewThreadReply.comment.url'
    ;;
  resolve)
    TID="${3:?thread-id required}"
    [[ "$TID" =~ ^[A-Za-z0-9_=-]+$ ]] || die "bad thread id: $TID"
    assert_thread_on_pr "$TID"
    gh api graphql -f query='mutation($t:ID!){resolveReviewThread(input:{threadId:$t}){thread{id isResolved}}}' \
      -F t="$TID" --jq '"resolved " + .data.resolveReviewThread.thread.id'
    ;;
  *)
    die "usage: $0 {list|reply|resolve} <pr> [thread-id] [body]"
    ;;
esac
