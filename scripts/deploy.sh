#!/usr/bin/env bash
# Deploy one exact commit to the local cluster.
#   TAG      git commit to deploy (default: current HEAD, short SHA)
#   SOURCE   ghcr  = pull the image CI built and pushed for that commit (default when TAG is given)
#            local = build the image here from the working tree (default when TAG is not given)
#   VALUES   extra values file (default: values-local.yaml, real model)
# helm --atomic rolls back by itself if the new pods never become ready.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=ghcr.io/kauxp/clinic-agent
if [ -n "${TAG:-}" ]; then SOURCE="${SOURCE:-ghcr}"; else TAG="$(git rev-parse --short=12 HEAD)"; SOURCE="${SOURCE:-local}"; fi
VALUES="${VALUES:-deploy/helm/values-local.yaml}"

if [ "$SOURCE" = ghcr ]; then
  docker pull "$REPO:$TAG"
else
  if [ -n "$(git status --porcelain)" ]; then TAG="$TAG-dirty"; fi   # never pass off uncommitted code as a commit
  docker build --build-arg APP_VERSION="$TAG" -t "$REPO:$TAG" .
fi
kind load docker-image "$REPO:$TAG" --name clinic

kubectl apply -f deploy/k8s/namespace.yaml
helm upgrade --install clinic-agent deploy/helm/clinic-agent -n clinic -f "$VALUES" \
  --set image.repository="$REPO" --set image.tag="$TAG" --set image.pullPolicy=Never \
  --atomic --wait --timeout 3m --description "image $REPO:$TAG"
helm history clinic-agent -n clinic --max 5
helm test clinic-agent -n clinic --logs
