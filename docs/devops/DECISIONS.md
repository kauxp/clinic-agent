# Decisions and trade-offs

Each choice, the alternative I considered, and when the alternative would be right.

## Workload: Deployment, 2 replicas, sticky sessions

- **Chosen:** a `Deployment` with 2 replicas and `sessionAffinity: ClientIP` on the Service.
- **Why:** the service keeps no data that must survive (the clinic database is demo data, rebuilt per chat). Two replicas let one pod restart or roll out while the other serves.
- **Not a StatefulSet:** pods don't need stable names or their own disks.
- **Cost of this choice:** a chat lives in one pod's memory. If that pod goes away, the chat is lost and the client must start a new session. Sticky sessions by client IP are also coarse (everyone behind one NAT lands on one pod).
- **When to change:** with real users, move session state to Redis (or a database) so any pod can serve any chat; then sticky sessions and the HPA restriction both go away.

## Health checks

- `startupProbe /healthz`: up to 60 s to boot before liveness starts.
- `livenessProbe /healthz`: "is the process alive" -> restart if not. It does **not** call Gemini, so a provider outage never restarts healthy pods.
- `readinessProbe /readyz`: "can it serve" -> policy loaded and model client configured (key present). An unready pod gets no traffic and blocks a rollout.

## Resources and scaling

- Requests 100m CPU / 192Mi, limit 1 CPU / 512Mi. The work is mostly waiting on the model API, so CPU is low; memory grows with chats, which is why `MAX_SESSIONS` caps chats per pod.
- HPA is in the chart but **off**: scaling down kills pods and their in-memory chats. Turn it on once sessions are external.
- PodDisruptionBudget `minAvailable: 1` and soft pod anti-affinity (spread across nodes).

## Configuration: one Helm chart, small values files

- `values.yaml` holds defaults; `values-local.yaml` (real model) and `values-ci.yaml` (scripted model) hold only differences. One chart, so environments can't drift into different copies.
- The ConfigMap's checksum is a pod annotation, so a config-only change also rolls the pods.
- **Not Kustomize:** Helm also gives release history and `helm rollback`, which I use for release control.

## CI/CD: GitHub Actions; automatic up to a tested image, deploy is a deliberate step

- **CI (every PR and push to main):** lint + tests -> secret scan, dependency audit, misconfiguration scan -> build image tagged with the commit SHA -> image vulnerability scan -> deploy that image into a throwaway kind cluster with the real chart and run `helm test`.
- **Artifact:** the image, tagged with the 12-character commit SHA. Pushed to GHCR only from `main`, so every pushed image matches a reviewed commit.
- **Deploy to my local cluster:** `make deploy TAG=<sha>` (or the `deploy` workflow on a self-hosted runner, behind the `local` environment's approval). Hosted GitHub runners can't reach a laptop; the approval point is "a human picks a commit that passed CI on main".
- **Not Argo CD / GitOps:** more moving parts than this project needs. With a shared cluster I'd use Argo CD so the cluster pulls the desired state from git and shows drift.

## Release control: rolling update + atomic Helm + rollback; policy version as a flag

- **Rolling update** with `maxSurge: 1, maxUnavailable: 0`: a new pod must be ready before an old one goes.
- `helm upgrade --atomic --wait`: if the new pods never become ready, Helm rolls back by itself.
- `helm rollback clinic-agent <rev>` (`make rollback`): for a release that is healthy but wrong.
- **Traceability:** image tag = commit SHA, required by the chart (no `latest`); the app reports it at `/version`, in `clinic_build_info` and the `app.kubernetes.io/version` label; `helm history` lists every release.
- **Feature flag:** `config.policyVersion` picks which agent policy (prompt rules + guards) is live. Changing it rolls out new agent behaviour without a new image, and rolling it back is a values change.
- **Not blue-green / canary:** they need a second full copy or a traffic-splitting layer (ingress controller, service mesh, Argo Rollouts). With one Service and two replicas, rolling + atomic rollback covers the risk. With real traffic, a canary on the policy version would be the next step, because agent behaviour is the riskiest change.
- **Drift:** everything comes from the chart; check with `helm diff upgrade` (plugin) or `kubectl diff`; never `kubectl edit` live objects.

## Observability

- **Metrics:** Prometheus client in the app; scraped via a ServiceMonitor. HTTP rate/errors/latency, model call latency and outcome, tool calls, committed writes, handoffs, active sessions, build info.
- **Logs:** JSON to stdout, one line per request and per turn, with request id, session id and trace id. **Patient messages are never logged** (health data). Read with `kubectl logs`; a log store (Loki) would be the next addition.
- **Traces:** OpenTelemetry -> Jaeger. One trace per request: HTTP span -> `agent.respond` -> one `llm.chat` span per model call, with tool calls as span events. Shows where the time goes (almost always the model).
- **SLOs:** 99.5% of chat requests without 5xx; p95 reply time under 10 s. Alerts in `PrometheusRule`, each linked to a runbook entry.

## Security

- **Secrets:** Gemini key only in a Kubernetes Secret, created from the environment / `.env` by a script that never prints it. CI needs no secret (scripted model). `.env` stays git-ignored; gitleaks scans history.
- **Runtime:** non-root uid 10001, read-only root filesystem, all Linux capabilities dropped, no privilege escalation, seccomp `RuntimeDefault`; the `clinic` namespace **enforces** the "restricted" Pod Security Standard, so a pod without these settings is refused.
- **Permissions:** own ServiceAccount with no token mounted (the app never calls the Kubernetes API). Workflows default to `contents: read`; only the build job gets `packages: write`.
- **Exposure:** ClusterIP only (reach it with port-forward); NetworkPolicy allows only DNS, HTTPS out, OTLP to Jaeger and scraping from `monitoring`.
- **Checks:** pip-audit (dependencies), trivy (image CVEs, Dockerfile and Helm misconfigurations), gitleaks (secrets).
- **Known gaps:** no authentication on the API (it is internal only); actions pinned to version tags, not commit SHAs; images not signed.
