#!/usr/bin/env bash
# Start ApplyPilot and open the dashboard. Ctrl+C to stop.
cd "$(dirname "$0")" && exec uv run applypilot start "$@"
