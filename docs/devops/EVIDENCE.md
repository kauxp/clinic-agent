# Evidence

Captured on the local kind cluster on 18 Oct 2026 from this branch. Raw command output is in [`evidence/`](evidence/).
Deployed version in all captures: image `ghcr.io/kauxp/clinic-agent:27464722832d` (commit `2746472`), scripted model
(`values-demo.yaml`), so the runs need no API key and are repeatable.

| Claim | Evidence |
|---|---|
| CI checks, builds and tests every change | GitHub Actions run of the `ci` workflow on this PR (link in the PR description): lint, tests, gitleaks, pip-audit, trivy, image build + scan, Helm deploy to kind, `helm test` |
| App runs on Kubernetes, 2 replicas on different nodes, probes pass | [01-cluster-and-release.txt](evidence/01-cluster-and-release.txt) |
| Running version is traceable to a commit | same file: image tag, `app.kubernetes.io/version` label and Helm release description all `27464722832d`; `/version` in [02](evidence/02-user-journey.txt); `clinic_build_info` in [04](evidence/04-metrics-alerts-traces.txt) |
| A user journey works end to end | [02-user-journey.txt](evidence/02-user-journey.txt): booking through a port-forward, `writes_committed: 1`; in-cluster `helm test` in [06](evidence/06-fault-bad-release-auto-rollback.txt) |
| Logs are structured, carry trace ids, and contain no patient text | [03-logs.txt](evidence/03-logs.txt) (the message "I'm Priya Shah, born 9 May 2001" was sent and does not appear) |
| Metrics are scraped, alert rules and SLO recording rules are loaded, traces reach Jaeger, dashboard is provisioned | [04-metrics-alerts-traces.txt](evidence/04-metrics-alerts-traces.txt): both pods `up`; 5 alerts + 3 recording rules; trace `942df2e3...` is the same `trace_id` as a log line in 03 |
| Security controls are enforced, not just configured | [05-security-controls.txt](evidence/05-security-controls.txt): root pod refused by Pod Security; uid 10001 + read-only filesystem; egress to port 80 blocked; another namespace can't reach the app while it can reach an unprotected service; no ServiceAccount token |
| A bad release can't take the service down and is undone automatically | [06-fault-bad-release-auto-rollback.txt](evidence/06-fault-bad-release-auto-rollback.txt) |
| Losing a pod doesn't fail requests | [07-fault-pod-killed.txt](evidence/07-fault-pod-killed.txt): 189 requests, 0 failed |
| Manual rollback to an older commit and forward again | [08-rollback-and-traceability.txt](evidence/08-rollback-and-traceability.txt) |

## Fault demonstration (symptom -> hypothesis -> evidence -> change -> verification)

Release revision 4 switched the model provider to `gemini` without an API key in the cluster.

1. **Symptom:** the rollout stalls; the new pod is `Running` but `0/1` ready; the two old pods keep serving (`maxUnavailable: 0`).
2. **Hypothesis:** the readiness check fails because the key is missing.
3. **Evidence:** `/readyz` on the new pod returns `503 {"reason": "GEMINI_API_KEY is not set (create the Kubernetes secret)"}`; pod events show `Readiness probe failed ... 503`.
4. **Change:** none needed by hand: `helm upgrade --atomic` timed out after 60 s and rolled back (revision 5, "Rollback to 3"). The real fix would be `make secret`, then deploy again.
5. **Verification:** 2/2 pods ready, `/version` = `27464722832d`, `helm test` passed.

## Limitations of this evidence

- Captured with the scripted model; with the real model, replies and latency differ but the path (tools, database, metrics) is the same.
- The SLO error-ratio series is empty in the capture because it needs several minutes of traffic.
- Screenshots of Grafana and Jaeger can be added from `make grafana` / `make jaeger`; the files above show the same data through their APIs.
