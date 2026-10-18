# Clinic scheduling agent + self-improving eval loop

A patient-appointment agent (book / reschedule / cancel over chat) and an evaluation harness that scores it on
**final database state and tool traces**, turns failures into structured policy patches, re-runs the suite, and
**promotes a patch only if nothing regresses**. Synthetic patients only.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env            # add GEMINI_API_KEY (free tier is fine)

python -m agent.cli --debug     # 1) talk to the agent (try: Priya Shah, 9 May 2001)
python -m evals.loop --runs 3   # 2) eval -> diagnose -> patch -> re-run -> gate -> report
python -m pytest -q             # offline tests: guards, graders, end-to-end with a fake LLM (no API key needed)
```

Also: `python -m evals.run_suite --runs 3 [--heldout] [--only id,id] [--judge]` scores one policy once.
Short demo of one loop closing (~10-15 min on the free tier):
`python -m evals.loop --only book_basic,emergency_midflow,cancel_basic,spouse_privacy --runs 1`.
Free-tier tip: `--runs 2`, `LLM_RPM=10`. Runs are checkpointed per file, so a daily-quota stop resumes where it left off.

## Run it as a service on Kubernetes

The agent also runs as an HTTP service (`service/`) with health checks, metrics, logs and traces, deployed to a
local kind cluster with Helm, built and tested by GitHub Actions. Quick start:

```bash
make cluster                                   # kind + Prometheus/Grafana/Alertmanager + Jaeger
VALUES=deploy/helm/values-demo.yaml make deploy  # scripted model, no API key needed
make smoke                                     # book an appointment through the API
```

Details: [run instructions](docs/devops/RUN.md) | [architecture](docs/devops/ARCHITECTURE.md) |
[decisions](docs/devops/DECISIONS.md) | [runbook](docs/devops/RUNBOOK.md) | [baseline](docs/devops/BASELINE.md) |
[evidence](docs/devops/EVIDENCE.md)

## Layout

```
agent/
  llm.py        provider adapter (Gemini); model = env var; retries/backoff; InfraError kept apart from agent failures
  world.py      fake clinic: in-memory SQLite, frozen clock, DB-level "one active booking per slot"
  tools.py      the 8 tools + all always-on code guards
  guards.py     catalog of toggleable guards (what the diagnoser may enable) + red-flag detector
  session.py    explicit state (verified? locked? pending? red flag?) re-injected every turn, outside chat history
  policy.py     versioned policy (prompt rules + guards + params); structured patch ops; diffs
  agent.py      hand-rolled tool loop, fail-safe handoff on step limit
  cli.py
policies/v0.json   the honest, minimal baseline (reasonable prompt, both toggleable guards OFF)
evals/
  scenarios.py  14 dev scenarios + 4 held-out paraphrase variants, tagged by failure mode
  simulator.py  reactive (rule-based) patients for most; LLM patients for adversarial/ambiguous
  graders.py    deterministic state/trace checks (the score)
  runner.py     resumable runs, infra errors excluded from scores
  judge.py      soft LLM judge (clarity/empathy), never gates; export + calibrate against hand labels
  diagnoser.py  failing runs -> validated structured diagnoses
  loop.py       the improvement loop + regression gate + ledger
  report.py     before/after tables by scenario and by category
tests/          24 offline tests
```

## Design in one paragraph

Safety is enforced in **code**; the prompt handles judgment. No tool accepts a patient id, so cross-patient access is
structurally impossible. Everything except `escalate_to_human` needs a verified session. Writes re-check the slot at commit
time and are idempotent (a retry after a lost response can't double-book). Three wrong birth dates lock the session, and a
verified session can't switch identity. Two further guards ship **off** in v0 and are what the loop can switch on:
`confirm_grounding` (two-phase writes: consent needs a later patient turn and a quote that must appear verbatim in the
patient's message) and `red_flag_interlock` (emergency keywords block all writes). See `DESIGN.md` for the rest.

## The improvement loop

1. Run the dev suite (N runs per scenario, since agents are stochastic).
2. Diagnoser (strongest model, once per iteration) emits `{failure_class, evidence_turns, root_cause, fix_layer, patch}`
   with `fix_layer in prompt | tool_guard | state_logic | code_proposal` and a stated preference for code guards.
3. The patch is a **structured op** (`enable_guard`, `add_prompt_rule`, `edit_prompt_rule`, `set_param`) applied to a new
   policy version. Rules that name people/doctors/dates are rejected as overfit. `code_proposal` goes to `proposals/` for a human.
4. Re-run the full suite. **Accept only if** target scenarios gain passes, no other scenario confirmed-regresses (a drop is
   re-run once to separate flake from regression), and safety/privacy rates don't fall. Rejections are fed back to the diagnoser.
5. The diff and score table are shown; a human promotes it (`--approve ask`, default). `auto` exists for CI/demo only.
6. After the loop, the held-out variants run on v0 vs final to show the fix generalises.

Outputs: `policies/vN.json`, `evals/ledger.jsonl` (what changed and why), `runs/loop_log.jsonl`, `runs/report.md`.

## Assumptions (made, not asked)

- Frozen clock: today is Wed 2026-10-07 13:00, so "next Tuesday" is reproducible.
- Verification is name + date of birth. Real clinics need stronger identity proofing; out of scope.
- Dead ends count as failures: a refused online cancellation must end in a human handoff, not "please call us".
- If v0 happens to pass everything with a strong model, make it weaker/harder (`--from-version`, scenario variants, higher
  temperature) rather than faking a failure. A baseline that can't fail shows nothing.

## What the eval cannot see (read before trusting a score)

- **PII leaks** are caught when names, birth dates or two of {date, time, doctor} appear literally. Paraphrases ("half past nine", "his usual doctor") and leaks spread across sentences are not.
- **Hallucinated availability** is only caught if it leads to a booking attempt. Free-text claims about times are unchecked.
- **Consent quality**: the grader checks that an affirmative patient message and a read-back preceded each write (regexes),
  not that the patient truly understood.
- **Simulator realism**: reactive patients are polite and keyword-driven; LLM patients drift. Real patients mumble, interrupt, and use other languages.
- **No voice, latency, or EHR integration quirks.** Single-channel text only.
- **Judge**: a transcript-only judge can't see state. It is soft-only, and `evals/judge.py calibrate` reports its agreement with hand labels.
- **Red-flag keywords are a safety net, not coverage.** Held-out variants test the paraphrase gap.
- Free-tier APIs may retain prompts: synthetic data only. Production needs a BAA-covered / zero-retention endpoint.
