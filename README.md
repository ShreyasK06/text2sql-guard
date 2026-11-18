# text2sql-guard

[![Query console showing an allowed request (revenue by region, as an analyst) with the eight-stage trace and the result table](docs/screenshots/console-allowed.png)](https://shreyask06.github.io/text2sql-guard/)

![python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue) ![license MIT](https://img.shields.io/badge/license-MIT-green) ![tests 1360 passed](https://img.shields.io/badge/tests-1402%20passed-brightgreen) ![live LLM small dev-set only](https://img.shields.io/badge/live%20LLM-small%20dev%20set%20only-yellow)

**text2sql-guard is a text-to-SQL service for PostgreSQL that treats the language model as untrusted.** A question (or raw SQL) is turned into a query and then passes eight stages, six of which can refuse it, before a read-only database user returns rows that are masked and scoped to the caller's role. Every refusal carries a rule id and a plain-language reason, every request is logged, and the whole policy lives in one file that also generates the database grants. It ships with a React web app (query console, audit log, policy explorer, results page, about page) and a recorded demo that runs without a backend.

**Live demo:** <https://shreyask06.github.io/text2sql-guard/>. This is the *recorded* demo, not a live service: GitHub Pages hosts only the static front end, which replays real responses captured earlier from the running API (mock provider, local Postgres). Nothing you click there is executed.

What it does **not** have, stated up front: no attack block rate was measured (the evaluation contains zero attack items), live LLM generation was checked only on a small 23-question dev set (see [Model benchmark](#model-benchmark)), and there is no authentication. See [Not validated](#not-validated).

## The web app

A Vite + React + TypeScript single-page app in `frontend/`, served by the same FastAPI process that hosts the API. With the full stack running (see [Quick start](#quick-start)) open `http://localhost:8000/`.

| Screen | What it shows |
|---|---|
| **Console** | Pick a role, ask a question (or type SQL), see the generated SQL with highlighting, an 8-stage trace (passed, blocked or not reached, with rule id, plain reason and per-stage latency), the masked result table and the verifier result. |
| **Audit log** | Every request, newest first, filterable by decision, rule and role, with the stage timings of each. |
| **Policy** | For each role, which tables and columns are visible, masked, or hidden, and the limits that apply. |
| **Safety and results** | The measured numbers from `eval/results`, with the missing attack rate stated plainly. |
| **About** | What the product is, how it fails closed, and what it does not guarantee. |

| Blocked request | Policy explorer |
|---|---|
| ![A request to delete all orders blocked at the input guard, later stages shown as not reached](docs/screenshots/console-blocked.png) | ![Policy explorer for the manager role showing visible, masked and hidden columns per table](docs/screenshots/policy-manager.png) |

| Safety and results | Landing page | Phone |
|---|---|---|
| ![Results page with the not-measured notice and false-block rates](docs/screenshots/safety.png) | ![Landing page hero with a real recorded trace](docs/screenshots/about.png) | ![Console on a phone-width screen](docs/screenshots/phone-blocked.png) |

### Modes: live and recorded demo

- **Live.** The app talks to the API (`VITE_API_BASE`, or the same origin). The header shows the provider and whether the database is up.
- **Recorded demo.** The app replays `frontend/public/data/demo.json`: real responses captured from the running service by `scripts/capture_demo.py`. It is labelled **"Recorded demo, not live"** on screen and nothing in it executes.
- **Auto-detect.** On load the app calls `/health`; if no API answers it falls back to the recorded demo and says so in the banner. `VITE_MODE=demo` forces the demo (this is what GitHub Pages builds); `?demo=1` forces it at runtime.

Fonts are bundled with the app (`@fontsource`), so it works offline. Every asset and data URL is built from Vite's base path, so the same build works at `/` or under `/text2sql-guard/`.

## Quick start

### Full stack with Docker (one port)

Requires Docker. The default provider is `mock`, a deterministic rule-based generator, so no API key is needed.

```
docker compose up --build        # PostgreSQL 16 + seed + API + the built React app
# open http://localhost:8000
```

The `postgres` service keeps its existing name and host port (54329). A one-shot `seed` service loads the synthetic data, then the `api` service starts and serves both the API and `frontend/dist`. All data is synthetic (Faker); the database password is a local dev value. (The Docker files were validated with `docker compose config` but not built or run on the machine that authored them, which was short on disk.)

### Development without Docker for the app

Requires Python 3.11+, Node 20+ and a PostgreSQL 16 demo database (`python scripts\db.py up` starts it with Docker; see `db/README.md`).

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt        # Windows; use .venv/bin/ elsewhere
.venv\Scripts\python scripts\db.py up                 # waits until the database is usable and seeds it if empty
.venv\Scripts\python -m uvicorn app.api.main:app      # API on http://127.0.0.1:8000

cd frontend
npm ci
npm run dev                                           # http://localhost:5173, auto-detects the API on :8000 via VITE_API_BASE
npm run typecheck && npm test                         # type check and unit/component tests
VITE_BASE=/text2sql-guard/ VITE_MODE=demo npm run build   # what GitHub Pages builds -> frontend/dist
npm run build                                         # default base './'; the API then serves frontend/dist at /
```

For `npm run dev` against a local API, copy `frontend/.env.example` to `frontend/.env.local` and set `VITE_API_BASE=http://localhost:8000` (CORS is open to localhost origins only). Without a database the app falls back to the recorded demo.

```
.venv\Scripts\python -m pytest -q -p no:cacheprovider      # tests that need PostgreSQL skip themselves when it is down
.venv\Scripts\python -m streamlit run ui\streamlit_app.py   # the original Streamlit demo, optional
.venv\Scripts\python scripts\reproduce.py                  # tests + ablation + figures + RESULTS.md
```

Refreshing the web data after a new evaluation run:

```
.venv\Scripts\python scripts\build_web_data.py                       # eval/results -> frontend/public/data/results.json
.venv\Scripts\python scripts\capture_demo.py --base http://127.0.0.1:8000   # re-record frontend/public/data/demo.json (mock provider only)
```

### Deployment

`.github/workflows/pages.yml` runs on every push to `main`: install, type check, test, build the demo-only site with `VITE_BASE=/text2sql-guard/`, and publish `frontend/dist` to GitHub Pages. `.github/workflows/ci.yml` runs the offline Python suite (no database) plus front-end lint, type check, tests and build.

## Architecture

```mermaid
flowchart LR
    B[Browser<br/>React app, HashRouter] -- /query /audit /policy /catalog /health --> A[FastAPI<br/>app/api]
    A --> P[Guard pipeline<br/>8 stages]
    P --> D[(PostgreSQL 16<br/>read-only roles, RLS)]
    A -- serves frontend/dist at / --> B
    B -. no API: replays .-> J[demo.json<br/>recorded responses]
    GH[GitHub Pages<br/>static build, demo mode] --> B
```

## How a request flows

```mermaid
flowchart LR
    Q[Question or SQL<br/>plus role and region] --> G1[1 Input guard]
    G1 --> G2[2 Schema retrieval]
    G2 --> G3[3 SQL generation<br/>untrusted model]
    G3 --> G4[4 Allowlist]
    G4 --> G5[5 AST rules]
    G5 --> G6[6 EXPLAIN cost gate]
    G6 --> G7[7 Sandboxed execution]
    G7 --> G8[8 Output guard]
    G8 --> R[Masked rows<br/>plus SQL and trace]
    G1 -. refuse .-> X[Block with rule id and reason]
    G4 -. refuse .-> X
    G5 -. refuse .-> X
    G6 -. refuse .-> X
    G7 -. refuse .-> X
    X --> A[(Audit log)]
    R --> A
```

| # | Stage | Where | What it does |
|---|---|---|---|
| 1 | Input guard | `app/guards/input_guard.py` | Screens the question for restricted-data intent for the role, destructive intent, instruction-override phrasing and encoded payloads. Pattern based. |
| 2 | Schema retrieval | `app/generation/` | Shows the model only the tables and columns the role may see. Not a gate. |
| 3 | SQL generation | `app/generation/` | Provider interface (`mock`, `nvidia` / `openai_compatible`, `anthropic`, `gemini`), self-check verifier and bounded self-correction. Not a gate. |
| 4 | Allowlist | `app/guards/sql_validator.py` | Default-deny tables, columns and functions per role, resolved on the sqlglot AST. |
| 5 | AST rules | `app/guards/sql_validator.py` | SELECT only, single statement, forced LIMIT, caps on joins, subqueries, depth and set operations, cartesian-join check, fail closed on parse errors. |
| 6 | EXPLAIN cost gate | `app/guards/cost_gate.py` | Runs EXPLAIN (never the query) and refuses plans above a cost or row ceiling. |
| 7 | Sandbox | `app/execution/executor.py`, `db/init/*.sql` | Read-only per-role Postgres users with column-level grants, row-level security (a manager sees one region), `statement_timeout`, row cap, dangerous functions revoked. |
| 8 | Output guard | `app/guards/output_guard.py` | Column masking by role, PII pattern scrubbing, cell-size cap, scan for injection text in returned data. |

The ablation harness treats stages 1 and 4 to 8 as six independently switchable layers (`input_guard`, `allowlist`, `ast_rules`, `cost_gate`, `sandbox`, `output_guard`). Design rules: default deny, fail closed, enforcement in code and database permissions and never in the prompt, every block carries a rule id and a reason. The contract for each module is in `docs/ARCHITECTURE.md`; the original spec is `docs/PLAN.md`.

## Using a free NVIDIA-hosted model

The default provider is the deterministic `mock`, which needs no key and sends nothing anywhere. To generate SQL with a real model, the repo can talk to any OpenAI-compatible chat endpoint; the NVIDIA-hosted catalog at build.nvidia.com is the documented path because it has a free tier.

1. Sign in at https://build.nvidia.com and generate an API key (it starts with `nvapi-`).
2. Copy `.env.example` to `.env` (git-ignored) and set `NVIDIA_API_KEY=nvapi-...` there. Never commit the key or paste it into a command line that is logged.
3. In the same file set `LLM_PROVIDER=nvidia`. A key alone does not switch it on. Optionally set `NVIDIA_MODEL`; if you leave it empty the app picks a code-capable instruct model from the live model list (a keyword heuristic, not a vetted choice) and tells you to set `NVIDIA_MODEL` if none looks suitable.
4. Start the database and API as in the quick start. `GET /llm/status` shows the provider, `GET /llm/models` lists the models your key can see, and the web header shows the provider badge.
5. Run the smoke test: `.venv\Scripts\python scripts\llm_smoke.py`. It lists models, runs three plain analytics questions through the full guard pipeline and prints question, SQL, decision and latency. Without a key it exits with a message and calls nothing.

Other endpoints: `LLM_PROVIDER=openai_compatible` with `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL`. The client sends temperature 0, a bounded `max_tokens`, retries 429 and 5xx responses with backoff (honouring `Retry-After`), and holds itself to `LLM_RATE_LIMIT_PER_MIN` requests per minute (default 30). When it is rate limited the API returns a structured `llm_error` (for example `rate_limited` with `retry_after_s`) and the pipeline refuses to run anything. The optional self-check, which costs a second call per question, is off by default for these providers (`LLM_SELF_CHECK=1` enables it) and is skipped when little rate-limit headroom is left.

Honest notes:

- **Rate limits and terms.** The free tier is shared and rate limited (roughly tens of requests per minute) and is described by NVIDIA as being for development and testing. Verify the current terms and limits on build.nvidia.com before relying on it.
- **Your data goes to NVIDIA.** Each question and the role-visible schema (only the tables and columns that role may see, never hidden ones) are sent to the provider; the optional self-check also sends the generated SQL. Query results are not sent. Do not point it at data you may not share. Few-shot examples are built from the role-visible schema only.
- **Model output is untrusted.** It goes through the same guard pipeline as any other SQL, unchanged. SQL quality of open models varies; expect refusals, wrong answers and self-correction retries.
- **Validated live only on a small set.** The provider, retries and extraction are unit-tested against `httpx.MockTransport` fakes and were also run against the live service for the [Model benchmark](#model-benchmark) (23 scored questions, one synthetic schema). That is a small dev-set check, not a general accuracy claim. The documented default model is `nvidia/nemotron-3.5-bolt-e4b`; the code still resolves model ids from the live list and does not hard-code it.

## Model benchmark

A small, honest check of SQL generation through the full guard pipeline (all guards on), run on 2026-11-18 against the free NVIDIA tier. Code and data: `eval/llm_bench/` (`questions.jsonl`, `run.py`, `results/`); full write-up in `eval/llm_bench/results/RESULTS.md`. Scoring is execution accuracy: the pipeline's masked, row-capped result for the model's SQL must equal the result of hand-written gold SQL run through the same pipeline (multiset; row order only where asked).

| model (id from the live `/models` list) | exec. accuracy, 23 questions | 95% Wilson CI | guardrail blocks | latency p50 / p95 |
|---|---|---|---|---|
| `nvidia/nemotron-3.5-bolt-e4b` | 23/23 | [85.7%, 100%] | 0 | 0.7 s / 1.4 s |
| `google/gemma-4-31b-it` | 23/23 | [85.7%, 100%] | 0 | 2.2 s / 10.2 s |
| `mistralai/mistral-large-4-1t-a52b` | 7/7 completed, 16 of 23 timed out at 60 s | n/a | 0 | not usable on the free tier at the time |
| `minimaxai/minimax-m3.1-flash` | not benchmarked: timed out (probe: about 90 s per trivial request) | n/a | n/a | n/a |
| `bigcode/starcoder2-15b` | not benchmarked: no chat endpoint (HTTP 404) | n/a | n/a | n/a |

Selected default: `NVIDIA_MODEL=nvidia/nemotron-3.5-bolt-e4b` (tied on accuracy with gemma, about 3x lower median latency). The two policy questions (email and salary as an analyst) were refused by the input guard before any model call for every model, so they test the guard, not the model.

Caveats, read these before quoting any number:

- **Small dev set.** n = 23 scored questions written by the repo author, easy to hard, one schema. A 23/23 result still has a Wilson interval that reaches down to about 86%, and it cannot separate the top two models. The models were not tuned on the set, but the questions were not hidden from whoever wrote the prompt.
- **One gold SQL was changed after seeing model output** (q10: group by product name instead of id; both models' reading was reasonable). That favours the two top models slightly. It is documented in `RESULTS.md`.
- **Latency and timeouts depend on free-tier load** and will vary run to run; each model was run once at temperature 0. The 60 s client timeout, not SQL quality, is what excluded the two slow models.
- Not a Spider/BIRD result, and not an attack test.

## Role and policy model

`policies/policy.yaml` is the single source of truth for the validator, the output guard, the generated database grants and the evaluation oracle. Anything not listed for a role is refused.

| Role | Sees | Row scope |
|---|---|---|
| `analyst` | Analytics columns only, no PII | all rows |
| `manager` | Customer name, city and similar, with email and phone masked | one region (row-level security) |
| `admin` | Broad reads across regions; credentials and secrets stay hidden | all rows |

The Policy screen in the app renders the exact column-level view for each role from the live policy. Role and region are **asserted by the caller** in this demo; a real deployment must derive them from an authenticated session.

## Evaluation method

- **Harness.** `eval/ablations/run_ablation.py` runs every item under 14 configurations: no guards, each layer alone (6), the full pipeline minus each layer (6), and all six. `eval/oracle.py` decides whether an attack achieved its goal. `eval/stats.py` provides percentile-bootstrap and Wilson intervals (the reported CIs are bootstrap). `eval/report.py` renders `eval/results/RESULTS.md` from the CSV and JSON files, with no hand-typed values; the web results page reads the same files through `scripts/build_web_data.py`.
- **Item files** (`eval/adversarial/v1/`): `dev.jsonl` (90 items) and `heldout.jsonl` (60 items), generated by hand-written Python builders in the same folder, written by the AI assistant that built this project, not by independent humans.
- **Item content: all 150 items are benign.** They are legitimate analytics questions with the SQL a competent analyst would write, chosen to be borderline (quoted identifiers, window functions, CTEs, joins, per-role column limits) so that they measure over-blocking. Dev: 90 benign (45 analyst, 25 manager, 20 admin). Held-out: 60 benign (30 analyst, 17 manager, 13 admin).
- **Held-out use.** `max_plan_cost` was tuned on the dev split. The held-out split was not used for that, but I cannot prove from the history that its items were never looked at while rules were written. Treat the held-out number as lightly held-out, not strictly blind.
- **Attack categories exist in the harness but have no items.** The harness, oracle and report are wired for destructive/escalation, exfiltration, prompt-injection and resource-abuse categories (`n = 0` in every one). The attack-set plan in `docs/PLAN.md` was not carried out.

## Results

Every number below is read from `eval/results/summary_dev.json`, `summary_heldout.json`, `latency_*.csv` and `RESULTS.md` (generated 2026-11-16 against the live Postgres container). Full pipeline, 95% bootstrap intervals. Dev and held-out are kept separate.

### Attack block rate: not measured

There are 0 attack items in either split. **No block rate, no per-category rate and no per-layer attack ablation exists for this project.** Spider/BIRD accuracy is also unmeasured.

### False-block rate (benign queries wrongly refused)

| split | benign n | false blocks | false-block rate [95% CI] | note |
|---|---|---|---|---|
| dev | 90 | 0 | 0.0% [0.0%, 0.0%] | used while tuning, so optimistic; the interval is degenerate |
| held-out | 60 | 3 | 5.0% [0.0%, 11.7%] | lightly held-out, not strictly blind |

The three held-out false blocks:

| item | layer | rule | reason |
|---|---|---|---|
| heldout-benign-017 | allowlist | `allowlist.function_denied` | function `exploding_generate_series` not in the allowed function list |
| heldout-benign-018 | ast_rules | `ast.cartesian_join` | comma join with no condition relating it to the other tables |
| heldout-benign-035 | cost_gate | `cost.explain_failed` | `pg_catalog.extract(unknown, integer)` does not exist |

Item 035 is partly a labelling problem: with every guard off it also fails in Postgres, so it is the item's SQL, not purely a guard decision. That is also why the "no guards" configuration shows 1 of 60 held-out false blocks. The other two are genuine over-blocking by the policy.

### Latency (benign queries, cache bypassed, mock provider so no model time)

| split | full pipeline median / p95 | no guards median / p95 | median overhead |
|---|---|---|---|
| dev (n=90) | 16.54 / 30.18 ms | 6.08 / 62.57 ms | 10.46 ms |
| held-out (n=60) | 18.09 / 42.58 ms | 9.76 / 60.16 ms | 8.33 ms |

Per-stage latencies are on the results page and in `eval/results/RESULTS.md`. The execution-accuracy comparison (guards on versus off) was not run.

### Tests

`pytest -q -p no:cacheprovider` on this commit: **1402 passed**, 0 failed with PostgreSQL up (1237 passed and 123 skipped without a database, which is what CI runs) (the evaluation snapshot above recorded 1356; three tests were added for the `/catalog` endpoint, the front-end mount and CORS). The suite covers each guard, the pipeline, the API, the harness, the oracle and statistics, plus database tests against PostgreSQL 16 (column grants, read-only roles, statement timeout, RLS by region, function revocations, seed integrity). These tests check rule behaviour on fixed examples; they are not a measured block rate.

## Residual risk

This is design analysis, not measurement:

- Blocking behaviour against adversarial SQL, jailbreaks and data-borne prompt injection is untested at scale.
- The input guard is pattern based; paraphrased intent can slip past it, so the later layers carry the load.
- Over-blocking is real: two of 60 held-out benign queries were refused by policy.
- The EXPLAIN gate relies on Postgres estimates, which can be wrong; the plan-cost ceiling was tuned on a small dev set and is sensitive to RLS plans.
- Role and region are caller-asserted in the demo API, web app and Streamlit UI; there is no authentication. Switching guard layers off through the API is disabled unless `T2S_ALLOW_LAYER_OVERRIDE=1`.
- Output masking is column and pattern based, so sensitive values in free-text columns are only partly covered.
- Single database engine (PostgreSQL 16), single synthetic schema.

## Not validated

- **Live LLM: validated only on a small dev set.** Live NVIDIA generation through the guards was run on 23 scored questions plus 2 policy questions for the models in the [Model benchmark](#model-benchmark) (two models completed, two were too slow on the free tier, one has no chat endpoint). Still not validated: the Anthropic and Gemini providers (fake transports only), the app's default-model keyword heuristic, larger or harder question sets, the optional self-check with a real model, and behaviour under sustained rate limiting. The web app and the recorded demo still use the `mock` provider.
- **No attack evaluation.** No adversarial items exist, including for live models: the benchmark above uses plain analytics questions only. No attack block rate, interval, per-category or per-layer attack ablation.
- **No Spider or BIRD run.** `eval/benchmarks/` is empty, so execution accuracy on public benchmarks and the accuracy cost of the guards (guards on versus off) are unmeasured; the only accuracy number is the 23-question dev set above.
- **Verifier and self-correction** (silent-wrong-answer rate, precision and recall, retry recovery) were not evaluated.
- **Postgres-dependent layers did run on real PostgreSQL 16** for the 150 benign queries and the database tests. That shows benign queries work through them and the grants, RLS and timeout behave as tested; it does not show they stop attacks.
- **Web app:** checked in one real browser (Chrome) at desktop and phone widths, in light mode, and with an automated accessibility audit (no violations reported on the console, About and results pages); dark mode, other browsers and load were not tested. The Docker images were not built.
- No API keys are needed to run the mock path, and none are present; there is no `.env` in the repository.

## Project layout

```
app/            pipeline, guards, executor, generation, audit log, FastAPI service (app/api)
frontend/       React + TypeScript web app (Vite); public/data holds demo.json and results.json
.github/        workflows: Pages deploy, CI
Dockerfile      API + built front end image (see docker-compose.yml)
policies/       policy.yaml, the single source of truth
db/             schema, seed data, generated grants, Docker init scripts
eval/           harness, oracle, statistics, ablations, results, llm_bench (live model benchmark), benchmarks (empty)
scripts/        db.py, reproduce.py, build_web_data.py, capture_demo.py, llm_smoke.py (live LLM check, needs a key)
tests/          offline and database tests
ui/             Streamlit demo
docs/           PLAN.md, ARCHITECTURE.md, figures, screenshots
```

## Roadmap

Not done, in rough priority order: author and review an independent attack set and report block rates with intervals; run a live model through the guards; run Spider/BIRD to measure the accuracy cost; replace caller-asserted roles with authenticated sessions; evaluate the verifier and self-correction loop; broaden masking beyond column and pattern rules.

## License

MIT, see `LICENSE`.
