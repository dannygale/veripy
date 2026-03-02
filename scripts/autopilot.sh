#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROMPT_FILE="$(dirname "$0")/autopilot-prompt.md"

cd "$DIR"

# Source project venv if it exists
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
fi

iteration=0
max_iterations="${1:-50}"

while true; do
    iteration=$((iteration + 1))
    echo "═══ Autopilot iteration $iteration / $max_iterations ═══"

    # Check completion: all features implemented, all todos resolved
    remaining_features=$(pm feature list 2>/dev/null | grep -cv 'implemented' || true)
    remaining_todos=$(pm todo list 2>/dev/null | grep -cv 'resolved\|wontfix' || true)

    echo "  Features remaining: $remaining_features"
    echo "  TODOs remaining:    $remaining_todos"

    if [ "$remaining_features" -eq 0 ] && [ "$remaining_todos" -eq 0 ]; then
        echo "✓ All features implemented and all tasks complete."
        break
    fi

    if [ "$iteration" -gt "$max_iterations" ]; then
        echo "✗ Reached max iterations ($max_iterations). Stopping."
        exit 1
    fi

    # Build prompt with current pm state injected
    feature_status=$(pm feature list 2>/dev/null || echo "(no features)")
    todo_status=$(pm todo list 2>/dev/null || echo "(no todos)")

    prompt="$(cat "$PROMPT_FILE")

## Current project status

### Features
\`\`\`
$feature_status
\`\`\`

### Tasks
\`\`\`
$todo_status
\`\`\`"

    kiro-cli chat --no-interactive --trust-all-tools "$prompt"

    echo ""
    echo "── Status after iteration $iteration ──"
    pm feature list 2>/dev/null || true
    pm todo list 2>/dev/null || true
    echo ""
    sleep 2
done
