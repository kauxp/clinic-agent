.PHONY: install agent eval loop test

install:
	pip install -r requirements.txt

agent:      ## chat with the agent
	python -m agent.cli --debug

eval:       ## score the latest policy once
	python -m evals.run_suite --runs 3

loop:       ## eval -> diagnose -> patch -> re-run -> gate -> report
	python -m evals.loop --runs 3

test:       ## offline: guards, graders, end-to-end with a fake LLM
	python -m pytest -q
