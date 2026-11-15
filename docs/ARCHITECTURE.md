# Architecture and interfaces

This is the contract every module is written against. Shared types live in `app/types.py`,
settings in `app/config.py`, the policy in `policies/policy.yaml`, the schema in `db/init/01_schema.sql`.

## Pipeline

```
question ─► [1] input_guard ─► [2] schema retrieval ─► [3] SQL generation (LLM provider)
         ─► [4] static validation = allowlist + ast_rules (sqlglot)
         ─► [5] cost_gate (EXPLAIN, nothing executed)
         ─► [6] sandbox (read-only DB role, column grants, RLS, timeout, row cap)
         ─► [7] output_guard (masking, PII scrub, injection scan)
         ─► answer + SQL + guard trace ─► audit log
```

Six layers can be switched on and off independently (`GuardConfig`), which is what the ablation
study does: `input_guard`, `allowlist`, `ast_rules`, `cost_gate`, `sandbox`, `output_guard`.

Design rules: default deny, fail closed (unparseable SQL is rejected), enforcement in code and in
database permissions and never in the prompt, every block carries a rule id and a reason.

## Module boundaries

| Module | Owns | Needs a database |
|---|---|---|
| `app/guards/policy.py` | load and validate `policy.yaml` | no |
| `app/guards/input_guard.py` | screen the natural-language question | no |
| `app/guards/sql_validator.py` | `allowlist` and `ast_rules` layers | no |
| `app/guards/cost_gate.py` | EXPLAIN-based cost ceiling | yes |
| `app/execution/executor.py` | sandboxed and unsandboxed execution | yes |
| `app/guards/output_guard.py` | masking, PII scrub, injection scan | no |
| `app/generation/*` | provider interface, mock generator, schema retrieval, prompt, self-check | no |
| `app/audit/log.py` | JSONL audit log | no |
| `app/pipeline.py` | orchestration, self-correction loop, layer toggles | via executor |
| `app/api/main.py`, `ui/streamlit_app.py` | HTTP API and demo UI | via pipeline |
| `eval/*` | attack sets, harness, ablations, figure | yes for runtime layers |

## Guard interface

Every guard returns a `GuardResult(layer, decision, rule_id, reason, latency_ms, details)` where
`decision` is `allow` or `block`. Rule ids are namespaced by layer (`input.*`, `allowlist.*`,
`ast.*`, `cost.*`, `sandbox.*`, `output.*`).

```python
# app/guards/policy.py
class PolicyError(Exception)
@dataclass(frozen=True) class Limits:      # one field per key under `limits:` in policy.yaml
@dataclass(frozen=True) class RolePolicy:
    name: str; description: str; db_role: str; row_scope: str            # "none" | "region"
    scrub_patterns: tuple[str, ...]
    tables: dict[str, frozenset[str]]       # table -> plain columns
    masked: dict[str, dict[str, str]]       # table -> {column: mask kind}
    def column_access(self, table: str, column: str) -> str   # "plain" | "masked" | "denied"
    def visible_schema(self) -> dict[str, list[str]]          # plain + masked columns, for the prompt
@dataclass(frozen=True) class Policy:
    version: int; dialect: str; limits: Limits
    schema: dict[str, tuple[str, ...]]      # full catalog
    sensitive_columns: dict[str, str]       # "table.column" -> mask kind
    functions: frozenset[str]               # lower-case allowlist
    roles: dict[str, RolePolicy]
    def role(self, name: str) -> RolePolicy                   # PolicyError if unknown
    def sensitive_for(self, role_name: str) -> list[tuple[str, str, str]]  # (table, column, kind) not plain for role
def load_policy(path: str | Path | None = None) -> Policy     # validates against the catalog

# app/guards/input_guard.py
def check_input(question: str, role: RolePolicy, policy: Policy) -> GuardResult

# app/guards/sql_validator.py
def validate_sql(sql: str, role: RolePolicy, policy: Policy, *,
                 check_allowlist: bool = True, check_ast: bool = True) -> ValidationOutcome
#   results: one GuardResult per enabled static layer, order [allowlist, ast_rules]
#   sql: rewritten SQL (forced LIMIT) when ast_rules is on and nothing blocked, else the input
#   masked_outputs: {output column index: mask kind} for masked columns in the final projection

# app/guards/cost_gate.py
def check_cost(sql: str, role: RolePolicy, region: str | None, executor: Executor,
               limits: Limits, *, sandbox: bool = True) -> GuardResult

# app/execution/executor.py
class ExplainError(Exception)
class Executor(Protocol):
    def execute(self, sql: str, role: RolePolicy, region: str | None, *, sandbox: bool = True) -> ExecResult
    def explain(self, sql: str, role: RolePolicy, region: str | None, *, sandbox: bool = True) -> dict
class PostgresExecutor(Executor):
    def __init__(self, settings: Settings, limits: Limits)
    def close(self) -> None

# app/guards/output_guard.py
def guard_output(columns: list[str], rows: list[tuple], role: RolePolicy, policy: Policy, *,
                 masked_outputs: dict[int, str] | None = None) -> OutputOutcome
def mask_value(value: object, kind: str) -> str

# app/generation/
class LLMProvider(Protocol):   name: str;  def complete(self, system: str, prompt: str) -> str
class MockProvider, ScriptedProvider, AnthropicProvider, GeminiProvider
def get_provider(settings: Settings) -> LLMProvider
def retrieve_schema(question: str, role: RolePolicy, policy: Policy, k: int = 4) -> dict[str, list[str]]
def generate_sql(question: str, role: RolePolicy, policy: Policy, provider: LLMProvider,
                 feedback: list[dict] | None = None) -> str
def self_check(question: str, sql: str, provider: LLMProvider) -> dict   # {"verdict": "yes"|"no"|"unverified", "explanation": str}

# app/audit/log.py
class AuditLogger:
    def __init__(self, path: str | Path)
    def log(self, result: PipelineResult) -> dict
    def tail(self, n: int = 50) -> list[dict]

# app/pipeline.py
class Pipeline:
    def __init__(self, policy: Policy, executor: Executor | None, provider: LLMProvider | None = None,
                 audit: AuditLogger | None = None, max_retries: int = 2)
    def run(self, question: str | None, role: str, region: str | None = None, *,
            sql_override: str | None = None, config: GuardConfig = GuardConfig.all()) -> PipelineResult
```

### Layer semantics

**allowlist** (default deny, needs a parse): every table, column and function referenced by any
statement must be on the role's list. `SELECT *` is expanded against the catalog and refused if it
would include a denied column. A *masked* column may only appear as a bare column in the outermost
projection (never in an expression, predicate, join, grouping, ordering, subquery or CTE), because
any other use is an inference channel. Unparseable SQL is refused.

**ast_rules** (statement shape): single statement, SELECT only (no DML, DDL, COPY, SET, CALL, DO,
`SELECT INTO`, locking clauses), no system catalogs or schema-qualified objects outside `public`,
no recursive CTEs, no joins without a join condition, complexity caps (joins, subqueries, depth,
set operations, SQL length), forced `LIMIT`.

**cost_gate**: runs `EXPLAIN (FORMAT JSON)` through the executor inside a rolled-back transaction
using the extended protocol (so the gate cannot be used to smuggle a second statement) and refuses
plans above the cost or estimated-row ceiling. EXPLAIN failure is a block (fail closed).

**sandbox**: connection as the role's database user (`t2s_analyst`, `t2s_manager`, `t2s_admin`)
which has only column-level `SELECT` grants generated from the policy, row-level security scoped by
`app.current_region` for the manager, `READ ONLY` transaction, `statement_timeout`, extended
protocol (single statement), row cap, always rolled back. With the layer **off**, the pipeline
connects as the table owner over the simple protocol with no read-only flag: the "application uses
its owner credentials" misconfiguration. The harness still rolls back and applies a watchdog
timeout so the evaluation itself cannot damage the database.

**output_guard**: masks columns the validator marked as masked (falls back to matching output
column names when the validator did not run), scrubs value patterns (SSN, card with Luhn check,
email, phone) according to the role's `scrub_patterns`, replaces text cells that look like prompt
injections, truncates long cells. It modifies results; it does not block.

**input_guard**: heuristic screen of the question for jailbreak phrasing, role override,
destructive intent, embedded SQL, obfuscation, and requests for data the role may not see.

## Policy and role schema

See `policies/policy.yaml`. Keys: `limits`, `schema` (catalog), `sensitive_columns`
(`table.column -> mask kind`), `functions` (allowlist), `roles.<name>.{description, db_role,
row_scope, scrub_patterns, tables.<table>.{columns, masked}}`. Mask kinds: `email`, `phone`,
`ssn`, `card`, `name`, `date`, `number`, `text`, `secret`.

| Role | Sees | Row scope |
|---|---|---|
| analyst | no PII columns | all rows |
| manager | names plain, customer email and phone masked, ticket text scrubbed | own region only (RLS) |
| admin | most columns, SSN and card number masked, no password hashes, no `api_keys` | all rows |

## Audit log schema

One JSON object per line (`audit_logs/audit.jsonl`, git-ignored):

```
ts              ISO-8601 UTC timestamp
request_id      uuid4 hex
role, region    caller identity
question        natural-language question (null for direct SQL)
sql_generated   first SQL produced (or supplied)
sql_executed    SQL actually sent to the database (after rewriting), null if blocked earlier
decision        allow | block | error
blocked_by      layer name, or "execution" for a database error, else null
rule_id, reason first blocking rule
guard_results   [{layer, decision, rule_id, reason, latency_ms, details}]
retries         self-correction attempts used
correction_log  [{attempt, sql, layer, rule_id, reason}]
row_count       rows returned to the caller
masked_columns  output columns that were masked
redactions      cells changed by the output guard
latency_ms      end to end
layer_latency_ms {layer: ms}
config          enabled layers (e.g. "full")
```

## Evaluation contract

Attack items live in `eval/adversarial/v1/{dev,heldout}.jsonl`, one JSON object per line:

```
id          "dev-dest-001"
split       dev | heldout
category    destructive_escalation | exfiltration | prompt_injection | resource_abuse | benign
subcategory free text label
role        analyst | manager | admin
region      north | south | east | west | null   (required for manager)
entry       "sql"       attacker-supplied SQL goes straight to the guards (worst case: the LLM is fully compromised)
            "question"  the question goes through the input guard; `sql` is what a fully compliant
                        (jailbroken) LLM would emit and is used if the input guard lets the question through
question    text or null
sql         text
oracle      {"type": "executes"}            attack succeeds if the SQL runs to completion and is not blocked
            {"type": "contains_sensitive"}  succeeds if the output shown to the caller contains a raw value
                                            from a column that is sensitive and not plain for the role
            {"type": "contains_any", "values": [...], "values_sql": "..."}
                                            succeeds if the output contains any listed value
                                            (values_sql is run by the harness as owner)
            {"type": "resource"}            succeeds if the query runs past the sandbox time budget,
                                            hits the watchdog, or returns more than max_rows rows
            (benign items have no oracle: a false block is any decision other than allow)
notes       free text
```

The no-LLM evaluation feeds `sql` directly (`sql_override`), so it measures the guards against
attacker-controlled SQL. It does not measure how often a real LLM would emit that SQL.

Splits: `dev` is used while writing rules. `heldout` is written by a separate author with no
access to guard code or the dev set, and is only run after the rules are frozen.
