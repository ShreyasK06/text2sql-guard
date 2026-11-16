# Demo database

PostgreSQL 16 in Docker on host port 54329, database `t2s`, synthetic data only.

## Commands

```
python scripts/db.py up       # compose up, wait for health, seed if empty
python scripts/db.py seed     # TRUNCATE and reload (deterministic)
python scripts/db.py reset    # down -v, up, seed (re-runs db/init/*.sql)
python scripts/db.py down
python scripts/db.py status
python -m db.gen_grants           # regenerate db/init/02_grants.sql from policies/policy.yaml
python -m db.gen_grants --check   # exit 1 if stale
```

Init scripts only run on a fresh data volume. After editing anything in `db/init/`, run `reset`.

## Roles

| Role | Purpose |
|---|---|
| `t2s_owner` | Owns the tables, not a superuser. "Sandbox off" in the evaluation means connecting as this role. Bypasses RLS. |
| `t2s_analyst`, `t2s_manager`, `t2s_admin` | App roles. Column-level SELECT only, read-only sessions, `statement_timeout` 2000 ms (equals `limits.statement_timeout_ms`), idle-in-transaction 10 s, lock timeout 1 s. |

All passwords are the dev value `t2s_local_dev_only` (see `app/config.py`).

## Init files (run in order)

- `00_roles.sql`: creates roles, locks down the database and `public` schema, sets per-role defaults, revokes PUBLIC execute on `pg_sleep*`, `set_config`, `pg_terminate_backend`, `pg_cancel_backend`, advisory locks, `lo_import/export` and the `*_to_xml` family (granted back to `t2s_owner` only). `SET LOCAL app.current_region = '...'` is a statement, so it still works.
- `01_schema.sql`: tables, created as `t2s_owner`.
- `02_grants.sql`: GENERATED from the policy. Plain and masked columns are granted; `api_keys` and `users.password_hash` are granted to nobody.
- `03_rls.sql`: row-level security. Analyst and admin see all rows; the manager sees only the region in `app.current_region` (users, orders, employees directly; payments and order_items via the parent order; support_tickets via the user). Unset or empty means no rows. RLS is not forced, so the owner bypasses it.

## Seed

`db/seed.py` (seeded Faker and `random`) loads users 2000, employees 300, products 200, orders 20000, order_items about 60000, payments 20000, support_tickets 3000, api_keys 10. Support tickets 1-30 and products 1-5 are overwritten from `db/seed_data/poison_dev.json` (data-borne prompt injection). If `db/seed_data/poison_heldout.json` exists (tickets 31-50, products 6-10) it is applied too.

## Tests

`pytest tests/test_db_sandbox.py tests/test_gen_grants.py` (the DB tests skip when the database is down).
