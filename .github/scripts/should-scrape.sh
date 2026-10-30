#!/usr/bin/env bash
# Decide whether this workflow run should scrape oakhouse.jp or reuse the deployed data.
# Prints "scrape=true|false" (append to $GITHUB_OUTPUT) and a one-line reason on stderr.
#
# Env: EVENT (github.event_name), DISPATCH_SCRAPE (inputs.scrape), REPO, BEFORE, SHA,
#      GH_TOKEN (for the compare API on pushes).
set -uo pipefail

zero=0000000000000000000000000000000000000000
case "${EVENT:-}" in
  workflow_dispatch)
    scrape="${DISPATCH_SCRAPE:-true}"; why="manual run" ;;
  push)
    if [ -z "${BEFORE:-}" ] || [ "$BEFORE" = "$zero" ]; then
      scrape=true; why="push without a previous commit to compare against"
    elif changed=$(gh api "repos/$REPO/compare/$BEFORE...$SHA" --jq '.files[].filename'); then
      # The parser or output format may have changed: old data could be stale or incompatible
      if grep -q '^scraper/' <<<"$changed"; then
        scrape=true; why="push changes files under scraper/"
      else
        scrape=false; why="push doesn't touch scraper/; reusing deployed data"
      fi
    else
      scrape=true; why="couldn't list the push's changed files"
    fi ;;
  *)
    scrape=true; why="event '${EVENT:-}'" ;;
esac

echo "scrape=$scrape"
echo "Scrape: $scrape ($why)" >&2
