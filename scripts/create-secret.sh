#!/usr/bin/env bash
# Put the Gemini API key into a Kubernetes Secret. Reads GEMINI_API_KEY from the environment or from .env.
# The key is piped straight to kubectl: never printed, never written to a file in the repo.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -z "${GEMINI_API_KEY:-}" ] && [ -f .env ]; then
  GEMINI_API_KEY="$(grep -E '^GEMINI_API_KEY=' .env | head -1 | cut -d= -f2-)"
fi
[ -n "${GEMINI_API_KEY:-}" ] || { echo "GEMINI_API_KEY not set (export it or put it in .env)" >&2; exit 1; }
kubectl -n clinic create secret generic clinic-agent-llm --from-literal=GEMINI_API_KEY="$GEMINI_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "secret clinic-agent-llm updated in namespace clinic"
