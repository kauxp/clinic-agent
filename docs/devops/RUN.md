# Run instructions

## Prerequisites

| Tool | Tested with |
|---|---|
| Docker | 29.1 |
| kind | 0.33 (Kubernetes 1.37 node image) |
| kubectl | 1.35 |
| Helm | 3.21 |
| Python + make (for local tests) | 3.12 |

Linux hosts running several kind clusters need higher inotify limits, otherwise worker nodes never join
(`inotify_init: too many open files` in the kubelet log):

```bash
sudo sysctl fs.inotify.max_user_instances=512 fs.inotify.max_user_watches=524288
```

## 1. Tests (no cluster, no API key)

```bash
make install
make lint test
```

## 2. Cluster with monitoring and tracing

```bash
make cluster        # kind (1 control plane + 2 workers), kube-prometheus-stack, Jaeger
```

## 3. Model key (optional)

Without it the pods start but stay un-ready (`/readyz` says why). For a keyless demo use the scripted model (step 4b).

```bash
echo "GEMINI_API_KEY=..." > .env    # git-ignored
make secret
```

## 4. Deploy

a) Real model, image built from the current commit:
```bash
make deploy
```
b) Scripted model (no key), with tracing and monitoring:
```bash
VALUES=deploy/helm/values-demo.yaml make deploy
```
c) The exact image CI built and pushed for a commit on `main`:
```bash
TAG=<12-char sha> make deploy
```
Every deploy runs `helm upgrade --atomic --wait` and then `helm test`.

## 5. Use and observe

```bash
make smoke            # a booking through a port-forward
make status           # helm history + pods
make grafana          # http://localhost:3000, dashboard "Clinic agent"; password: make grafana-password
make prometheus       # http://localhost:9090, alerts under Alerts
make jaeger           # http://localhost:16686, service "clinic-agent"
kubectl -n clinic logs deploy/clinic-agent -f
```

## 6. Roll back

```bash
make rollback                 # previous release
REVISION=3 make rollback      # a specific one (see make status)
```

## Clean up

```bash
make down
```

## Known limitations

- Chats live in pod memory: a pod restart or rollout ends the chats on that pod.
- The clinic database is per-chat demo data, frozen at Wed 2026-10-07 13:00.
- No authentication on the API; it is reachable only inside the cluster or through port-forward.
- Deploying from GitHub to the local cluster needs a self-hosted runner (`deploy` workflow); otherwise deploy with `make deploy`.
- Logs are read with `kubectl logs`; there is no log store (Loki) in this setup.
