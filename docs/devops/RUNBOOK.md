# Runbook

## What "healthy" means

| Signal | Healthy | Where |
|---|---|---|
| Ready pods | 2 of 2 | `kubectl -n clinic get deploy clinic-agent` |
| Chat error ratio (5xx) | < 0.5% (SLO 99.5%) | Grafana "Error ratio", `clinic:chat_errors:ratio5m` |
| Reply time p95 | < 10 s | Grafana "Agent reply time", `clinic:reply_latency:p95_5m` |
| Model calls | no `infra_error` | `clinic_llm_calls_total` |
| Running version | the commit you deployed | `curl /version`, `helm history` |

Verify recovery the same way every time: pods ready, `/version` shows the expected commit, `make smoke`
(or `helm test clinic-agent -n clinic --logs`) passes, and the error ratio panel returns to ~0.

## ClinicAgentHighErrorRate

More than 0.5% of chat requests return 5xx for 5 minutes.
1. `kubectl -n clinic logs deploy/clinic-agent --tail=100 | grep '"status": 5'` -> which route, which error.
2. If the log says `llm_unavailable` -> see ClinicAgentModelUnavailable.
3. If it started right after a deploy (`helm history`) -> `make rollback`, then verify recovery.
4. If `too many active chats` -> raise `config.maxSessions` or replicas.

## ClinicAgentSlowReplies

p95 reply time above 10 s for 10 minutes.
1. Grafana "Model call time p95": if the model is slow, it's the provider -> nothing to fix in the cluster; consider a faster model (`config.agentModel`).
2. If model time is fine but replies are slow -> open a slow trace in Jaeger (`make jaeger`) and see how many `llm.chat` spans one reply needed (a policy change can make the agent loop more).
3. Check CPU throttling: `kubectl -n clinic top pods`.

## ClinicAgentModelUnavailable

Model calls fail after retries.
1. `kubectl -n clinic logs deploy/clinic-agent | grep llm_unavailable` -> 429 means quota, 5xx means provider outage, 401/403 means key.
2. Quota: lower `config.llmRequestsPerMinute` or replicas. Key: `make secret`, then `kubectl -n clinic rollout restart deploy/clinic-agent`.
3. Provider outage: wait; the app already answers 503 with `Retry-After`.

## ClinicAgentNoReadyPods

No pod is ready.
1. `kubectl -n clinic get pods` and `kubectl -n clinic describe pod <pod>` (events).
2. `curl /readyz` through a port-forward -> the reason (e.g. missing `GEMINI_API_KEY` -> `make secret`).
3. If caused by a release -> `make rollback`.

## ClinicAgentEmergencyHandoff

Informational: the agent handed a possible emergency to staff. Make sure someone checks the handoff queue now.
