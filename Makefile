.PHONY: install agent eval loop test lint serve image cluster secret deploy smoke rollback status grafana prometheus jaeger grafana-password down

install:    ## local dev environment
	pip install -r requirements.lock -r requirements-dev.txt

agent:      ## chat with the agent in the terminal
	python -m agent.cli --debug

eval:       ## score the latest policy once
	python -m evals.run_suite --runs 3

loop:       ## eval -> diagnose -> patch -> re-run -> gate -> report
	python -m evals.loop --runs 3

test:       ## offline tests: guards, graders, the HTTP service (no API key)
	python -m pytest -q

lint:
	ruff check .

serve:      ## run the HTTP service locally with the scripted model
	LLM_PROVIDER=scripted uvicorn service.app:create_app --factory --port 8000

image:      ## build the container image for the current commit
	docker build --build-arg APP_VERSION=$$(git rev-parse --short=12 HEAD) -t ghcr.io/kauxp/clinic-agent:$$(git rev-parse --short=12 HEAD) .

cluster:    ## kind cluster + Prometheus/Grafana/Alertmanager + Jaeger
	./scripts/cluster-up.sh

secret:     ## Gemini key from .env into a Kubernetes Secret
	./scripts/create-secret.sh

deploy:     ## deploy: TAG=<sha> pulls the CI image from GHCR; no TAG builds the current commit locally
	./scripts/deploy.sh

smoke:      ## user journey through a port-forward
	./scripts/smoke.sh

rollback:   ## back to the previous release (or REVISION=n)
	helm rollback clinic-agent $(REVISION) -n clinic --wait
	helm history clinic-agent -n clinic --max 5

status:
	helm history clinic-agent -n clinic --max 5; kubectl -n clinic get deploy,pods,svc -o wide

grafana:    ## http://localhost:3000 (user admin, password: make grafana-password)
	kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80

grafana-password:
	@kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d; echo

prometheus: ## http://localhost:9090
	kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090

jaeger:     ## http://localhost:16686
	kubectl -n observability port-forward svc/jaeger 16686:16686

down:
	kind delete cluster --name clinic
