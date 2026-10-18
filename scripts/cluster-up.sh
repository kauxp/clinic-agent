#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
KPS_VERSION=92.10.1

if ! kind get clusters | grep -qx clinic; then
  kind create cluster --config deploy/kind/cluster.yaml
fi
kubectl config use-context kind-clinic >/dev/null

kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/observability/jaeger.yaml

kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -
if ! kubectl -n monitoring get secret grafana-admin >/dev/null 2>&1; then
  kubectl -n monitoring create secret generic grafana-admin \
    --from-literal=admin-user=admin --from-literal=admin-password="$(openssl rand -hex 16)"
fi
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update prometheus-community >/dev/null
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack --version "$KPS_VERSION" \
  -n monitoring -f deploy/observability/kube-prometheus-stack-values.yaml --wait --timeout 10m

kubectl -n observability rollout status deploy/jaeger --timeout 3m
echo "cluster ready: kubectl get nodes; next: make secret (optional) and make deploy"
