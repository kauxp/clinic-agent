# Baseline (before this PR)

Baseline commit: `d0585564c6f8574aba24e31766f226d45d53afee` (`main`, "Add evaluation suite for policy testing").

What the project was at that commit:

- A clinic scheduling **agent** (book, reschedule, cancel) that runs as a **terminal chat** (`python -m agent.cli`).
- An **evaluation loop** (`evals/`) that scores the agent on database state and tool traces and proposes policy patches.
- **Offline tests** (`pytest`, fake LLM, no API key).
- Configuration through a local `.env` file (Gemini key and model names).

DevOps features that already existed:

| Area | At baseline |
|---|---|
| Packaging | `requirements.txt` with unpinned lower bounds; no container image |
| Running | Only on a laptop, in the terminal; no network service, no health checks |
| CI/CD | None (tests run by hand with `make test`) |
| Kubernetes / deployment config | None |
| Release control | None; no versioning of what is running |
| Observability | `print` output in the terminal; `runs/` JSON files from the eval loop |
| Security | `.env` in `.gitignore`; code-level safety guards in the agent itself |
