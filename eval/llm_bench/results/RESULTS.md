# LLM SQL-generation benchmark (small dev set)

**Read this first.** This is a small development benchmark: 23 scored questions plus 2 policy questions on one synthetic schema, written by the repo author, with hand-written gold SQL. The models were not tuned on it, but the questions were visible to whoever wrote the prompt. With n this small the 95% bootstrap intervals are wide and differences of a few questions are noise. Latency and failures vary with free-tier load. Each model was run once at temperature 0.

Run: 2026-11-18 02:54 UTC, provider `nvidia`, 75 HTTP requests in total (cap 400), pipeline max retries 2, guards: all on.

## Per model

| model | exec. accuracy (strict) | 95% bootstrap CI | 95% Wilson CI | correct if column order is ignored | guardrail blocks | errors | latency p50 / p95 (s) |
|---|---|---|---|---|---|---|---|
| `nvidia/nemotron-3.5-bolt-e4b` | 23/23 = 100.0% | [100.0%, 100.0%] | [85.7%, 100.0%] | 23/23 | 0 | 0 (provider: 0) | 0.7 / 1.4 |
| `google/gemma-4-31b-it` | 23/23 = 100.0% | [100.0%, 100.0%] | [85.7%, 100.0%] | 23/23 | 0 | 0 (provider: 0) | 2.2 / 10.2 |
| `mistralai/mistral-large-4-1t-a52b` | 7/23 = 30.4% | [13.0%, 47.8%] | [15.6%, 50.9%] | 7/23 | 0 | 16 (provider: 16) | 60.0 / 60.1 |
| `bigcode/starcoder2-15b` | 0/3 = 0.0% | [0.0%, 0.0%] | [0.0%, 56.1%] | 0/3 | 0 | 3 (provider: 3) | 0.0 / 0.1 |
| `minimaxai/minimax-m3.1-flash` | 0/3 = 0.0% | [0.0%, 0.0%] | [0.0%, 56.1%] | 0/3 | 0 | 3 (provider: 3) | 60.0 / 60.0 |

Strict = result rows compared positionally (column order matters) as a multiset, order only when the question asks for one. Numbers are compared at 2 decimals. Guardrail blocks are accuracy questions on which the pipeline refused the model's SQL (counted as wrong); some are the model writing disallowed SQL, not a wrongly blocked correct query, see the notes below. Errors are non-block failures (provider, execution, no SQL). Latency is the full pipeline call per question, including any self-correction retries.

## Policy-respecting outcomes (questions that need data the role cannot see)

| model | refused by guard | refused by model | answered without hidden data | leak suspect | other error |
|---|---|---|---|---|---|
| `nvidia/nemotron-3.5-bolt-e4b` | 2 | 0 | 0 | 0 | 0 |
| `google/gemma-4-31b-it` | 2 | 0 | 0 | 0 | 0 |
| `mistralai/mistral-large-4-1t-a52b` | 2 | 0 | 0 | 0 | 0 |
| `bigcode/starcoder2-15b` | 0 | 0 | 0 | 0 | 0 |
| `minimaxai/minimax-m3.1-flash` | 0 | 0 | 0 | 0 | 0 |

No outcome here is scored right or wrong: refusing and answering without the hidden column are both policy-respecting; `leak suspect` would be a failure. Whether an answer that silently dropped or substituted a column is useful is a human judgment, see `results/*.jsonl`.

## Items every model failed

none

## Notes on failures and gold-SQL review

- **Gold fix (q10).** The first gold SQL grouped by `p.id, p.name`. Both gemma-4-31b-it and nemotron-3.5-bolt-e4b grouped by `p.name`, which merges products that share a name (the demo catalog has duplicate names). The question says 'which five products ... return product name', so grouping by name is a reasonable reading (and matches the repo's own few-shot example). The gold was changed to `GROUP BY p.name` and the stored model SQL was re-scored without new model calls (`--rescore`). This flipped q10 from wrong to right for both models. It was decided after seeing the model outputs, so treat the 100% figures as optimistic for this reason.
- **Both top models score 23/23.** With zero failures the bootstrap CI is degenerate ([100%, 100%]); the Wilson interval is shown beside it and is the more honest one. The set is too easy and too small to separate these two models on accuracy.
- **Guardrail blocks: 0 for every model** on the accuracy items. No false blocks were observed on these 23 questions, which are plain analytics queries; this says nothing about harder SQL.
- **Policy questions (p01 email, p02 salary):** every model that ran was stopped by the input guard (`input.restricted_data_intent`) before any model call, so these two items test the guard, not the model. No leak.
- **minimax-m3.1-flash: not benchmarked.** The first 3 requests hit the app client's 60 s timeout (provider error `network`) so the model was skipped. A separate one-off probe with a 150 s timeout showed it answers a trivial 16-token request correctly but only after about 90 s (free-tier queueing), and it returns reasoning text in a separate field.
- **mistral-large-4-1t-a52b: availability-bound.** 16 of 23 requests exceeded the 60 s client timeout and are counted as errors (not wrong SQL). The 7 that completed were all correct (7/7), so 7/23 mostly reflects free-tier latency (probe: about 92 s for a trivial request), not SQL quality. Do not read 30% as its accuracy.
- **starcoder2-15b: not a chat model on this endpoint.** `/chat/completions` returned `bad_model` (404) on the first 3 items, so it was skipped. The probe confirmed HTTP 404.
- **First attempt aborted on a harness bug, then resumed.** The first run's shared client-side limiter (max wait 20 s) raised 'rate_limited' for gemma q07-q09 (0 HTTP requests were sent for those items; the server never returned a 429), which tripped the 3-in-a-row abort. This exercised the abort path for real. The limiter wait was raised to 70 s and the run was resumed with `--resume --retry-errors`; nemotron's finished records were kept.

## Files

`questions.jsonl` (items and gold SQL), `results/<model>.jsonl` (raw per-item records: SQL, decision, rule, rows, correctness, latency, tokens when returned, provider error), `results/summary.json`. Re-run: `.venv\Scripts\python -m eval.llm_bench.run`.
