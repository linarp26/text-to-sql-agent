-- Read-only role used by the agent. This is the primary safety boundary:
-- even if the app-level SQL guard were bypassed, this role cannot modify data.
-- NOTE: the password here is a local-dev default; change it for any shared deployment.

CREATE ROLE analyst_ro LOGIN PASSWORD 'analyst_ro_dev_pw'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM analyst_ro;
GRANT USAGE ON SCHEMA public TO analyst_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO analyst_ro;

-- Defense in depth: every session for this role is read-only and time-boxed.
ALTER ROLE analyst_ro SET default_transaction_read_only = on;
ALTER ROLE analyst_ro SET statement_timeout = '5s';
ALTER ROLE analyst_ro SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE analyst_ro CONNECTION LIMIT 20;
