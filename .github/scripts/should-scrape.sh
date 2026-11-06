#!/usr/bin/env bash
# Decide whether this workflow run should scrape the listing sites or reuse the deployed data.
# Prints "scrape=true|false" (append to $GITHUB_OUTPUT) and a one-line reason on stderr.
#
# Pushes never scrape, whatever files they touch: scraper changes take effect at the next daily
# run (or a manual run with "scrape" ticked).
#
# Env: EVENT (github.event_name), DISPATCH_SCRAPE (inputs.scrape).
set -uo pipefail

case "${EVENT:-}" in
  workflow_dispatch)
    scrape="${DISPATCH_SCRAPE:-true}"; why="manual/daily run" ;;
  push)
    scrape=false; why="push: reusing deployed data (scrapes run on the daily schedule)" ;;
  *)
    scrape=true; why="event '${EVENT:-}'" ;;
esac

echo "scrape=$scrape"
echo "Scrape: $scrape ($why)" >&2
