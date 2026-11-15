-- Roles and hardening. Runs first, as the postgres superuser, inside the t2s database.
-- Dev password only: this is a throwaway database with synthetic data.

CREATE ROLE t2s_owner   LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB PASSWORD 't2s_local_dev_only';
CREATE ROLE t2s_analyst LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOINHERIT PASSWORD 't2s_local_dev_only';
CREATE ROLE t2s_manager LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOINHERIT PASSWORD 't2s_local_dev_only';
CREATE ROLE t2s_admin   LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB NOINHERIT PASSWORD 't2s_local_dev_only';

-- Database: only the four roles may connect; nobody gets TEMP via PUBLIC.
DO $$
BEGIN
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO t2s_owner, t2s_analyst, t2s_manager, t2s_admin', current_database());
END $$;

-- Schema: the owner may create objects; app roles may only look objects up.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT ALL ON SCHEMA public TO t2s_owner;
GRANT USAGE ON SCHEMA public TO t2s_analyst, t2s_manager, t2s_admin;

-- Per-role session defaults for the three app roles (the owner is deliberately left alone:
-- "sandbox off" in the evaluation means connecting as t2s_owner).
-- statement_timeout must equal limits.statement_timeout_ms in policies/policy.yaml
-- (checked by tests/test_gen_grants.py).
ALTER ROLE t2s_analyst SET statement_timeout = '2000ms';
ALTER ROLE t2s_manager SET statement_timeout = '2000ms';
ALTER ROLE t2s_admin   SET statement_timeout = '2000ms';
ALTER ROLE t2s_analyst SET default_transaction_read_only = on;
ALTER ROLE t2s_manager SET default_transaction_read_only = on;
ALTER ROLE t2s_admin   SET default_transaction_read_only = on;
ALTER ROLE t2s_analyst SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE t2s_manager SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE t2s_admin   SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE t2s_analyst SET lock_timeout = '1s';
ALTER ROLE t2s_manager SET lock_timeout = '1s';
ALTER ROLE t2s_admin   SET lock_timeout = '1s';

-- Dangerous built-ins: PUBLIC loses EXECUTE, only t2s_owner gets it back.
-- All overloads are matched by name. Note: SET LOCAL app.current_region = '...' is a
-- statement, not the set_config() function, so the executor's region handling still works.
DO $$
DECLARE
    f regprocedure;
BEGIN
    FOR f IN
        SELECT p.oid::regprocedure
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'pg_catalog'
          AND (
              p.proname IN (
                  'pg_sleep', 'pg_sleep_for', 'pg_sleep_until', 'set_config',
                  'pg_terminate_backend', 'pg_cancel_backend',
                  'lo_import', 'lo_export',
                  'query_to_xml', 'query_to_xml_and_xmlschema', 'query_to_xmlschema',
                  'table_to_xml', 'table_to_xml_and_xmlschema', 'table_to_xmlschema',
                  'cursor_to_xml', 'cursor_to_xmlschema',
                  'schema_to_xml', 'schema_to_xml_and_xmlschema', 'schema_to_xmlschema',
                  'database_to_xml', 'database_to_xml_and_xmlschema', 'database_to_xmlschema'
              )
              OR p.proname LIKE 'pg\_advisory\_%'
              OR p.proname LIKE 'pg\_try\_advisory\_%'
          )
    LOOP
        EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC', f);
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO t2s_owner', f);
    END LOOP;
END $$;
