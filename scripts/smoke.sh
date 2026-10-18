#!/usr/bin/env bash
set -euo pipefail
PORT="${PORT:-8080}"
kubectl -n clinic port-forward svc/clinic-agent "$PORT":80 >/dev/null 2>&1 &
PF=$!; trap 'kill $PF' EXIT
for _ in $(seq 20); do curl -sf "localhost:$PORT/healthz" >/dev/null && break; sleep 0.5; done
B="http://localhost:$PORT"
echo "version: $(curl -sf $B/version)"
SID=$(curl -sf -XPOST "$B/v1/sessions" | python3 -c 'import sys,json; print(json.load(sys.stdin)["session_id"])')
for msg in "Hi, I need a GP appointment next week, mornings please" "I'm Priya Shah, born 9 May 2001" "Yes, please book it"; do
  echo "patient: $msg"
  curl -sf -XPOST "$B/v1/sessions/$SID/messages" -H 'content-type: application/json' -d "{\"text\": \"$msg\"}" \
    | python3 -c 'import sys,json; r=json.load(sys.stdin); print("agent:  ", r["reply"]); print("        writes_committed:", r["writes_committed"])'
done
