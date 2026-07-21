"""
Postgres client for qa-logs. Read-only, enforced three independent ways:

  1. `assert_read_only` statically rejects anything that isn't a bare
     SELECT/WITH/EXPLAIN/SHOW/TABLE statement, rejects stacked statements
     (a second `;`-separated statement), and rejects any embedded
     write/DDL keyword (catches DML hidden inside a CTE).
  2. Every session sets `default_transaction_read_only = on` right after
     connecting, so Postgres itself refuses any write that slips past the
     guard above.
  3. `statement_timeout` caps how long a single query can run against a
     live (possibly production) database.

Optional SSH bastion tunnel when the database isn't directly reachable from
this machine — configure `DB_SSH_*` in config/db/.env. Omit those keys
entirely for a directly-reachable DB.

The tunnel shells out to the system `ssh` binary (`ssh -N -L ...`, kept
alive as a child process for the client's lifetime) rather than using the
`sshtunnel` package — `sshtunnel` 0.4.0 (its last release) calls
`paramiko.DSSKey`, which modern `paramiko` (>=3.3) no longer has, so it's
broken out of the box against a current paramiko. Shelling out to `ssh`
also matches the one battle-tested way this kind of bastion tunnel is
already known to work.
"""

from __future__ import annotations

import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

import psycopg2

from shared.config_loader import load_module_env
from shared.paths import cache_dir, ensure_dir

_WRITE_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE",
    "GRANT", "REVOKE", "VACUUM", "REINDEX", "COPY", "CALL", "MERGE",
    "LOCK", "COMMENT", "SECURITY", "DISCARD", "LISTEN", "NOTIFY",
    "REFRESH", "CLUSTER",
)
_WRITE_KEYWORD_RE = re.compile(r"\b(" + "|".join(_WRITE_KEYWORDS) + r")\b", re.IGNORECASE)
_ALLOWED_FIRST_WORD = {"SELECT", "WITH", "EXPLAIN", "SHOW", "TABLE"}
_INTO_RE = re.compile(r"\bSELECT\b.*?\bINTO\b", re.IGNORECASE | re.DOTALL)
_FIRST_WORD_RE = re.compile(r"[A-Za-z]+")


class ReadOnlyViolation(Exception):
    """Raised when a query fails the read-only guard — never sent to Postgres."""


def assert_read_only(sql: str) -> None:
    stripped = (sql or "").strip()
    if not stripped:
        raise ReadOnlyViolation("empty query")

    body = stripped[:-1] if stripped.endswith(";") else stripped
    if ";" in body:
        raise ReadOnlyViolation("stacked statements are not allowed — one SELECT per call")

    match = _FIRST_WORD_RE.match(body)
    first_word = match.group(0).upper() if match else ""
    if first_word not in _ALLOWED_FIRST_WORD:
        raise ReadOnlyViolation(
            f"only SELECT/WITH/EXPLAIN/SHOW/TABLE are allowed, got '{first_word or body[:20]!r}'"
        )

    if _WRITE_KEYWORD_RE.search(body):
        raise ReadOnlyViolation(
            "query contains a write/DDL keyword — rejected even if it's inside a CTE/subquery"
        )

    if _INTO_RE.search(body):
        raise ReadOnlyViolation("'SELECT ... INTO' creates a table — rejected")


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"SSH tunnel did not open local port {port} within {timeout}s")


class DBClient:
    def __init__(self, account: str = "default"):
        env = load_module_env("db", account)
        host = env.get("DB_HOST", "")
        name = env.get("DB_NAME", "")
        user = env.get("DB_USER", "")
        if not (host and name and user):
            raise RuntimeError(
                f"Set DB_HOST/DB_NAME/DB_USER (+ DB_PASSWORD) for account '{account}' "
                "in config/db/.env (bare keys, or DB__<ACCOUNT>__* for multi-account, "
                "e.g. DB__DEV__HOST / DB__PROD__HOST)."
            )
        self.account = account
        self.db_name = name
        self._db_user = user
        self._db_password = env.get("DB_PASSWORD", "")
        self._connect_host = host
        self._connect_port = int(env.get("DB_PORT") or 5432)
        self._statement_timeout_ms = int(env.get("DB_STATEMENT_TIMEOUT_MS") or 15000)
        self._default_row_limit = int(env.get("DB_ROW_LIMIT") or 500)
        self._tunnel_proc: Optional[subprocess.Popen] = None
        self._conn = None

        ssh_host = env.get("DB_SSH_HOST")
        if ssh_host:
            self._open_tunnel(ssh_host, env)

    # ------------------------------------------------------------------
    # Connection / tunnel
    # ------------------------------------------------------------------

    def _prepare_key(self, ssh_key_path: str, passphrase: Optional[str]) -> str:
        """Passphrase-protected keys can't be used non-interactively by a plain
        `ssh` subprocess (no TTY to prompt on). Mirrors the same trick this
        product's own DB helper scripts use: cache a passphrase-stripped copy
        once, then reuse it — `ssh-keygen -p` never touches the original."""
        if not passphrase:
            return ssh_key_path
        cache = ensure_dir(cache_dir("db")) / f"{self.account}-sshkey"
        if not cache.exists():
            import shutil
            shutil.copy(ssh_key_path, cache)
            cache.chmod(0o600)
            subprocess.run(
                ["ssh-keygen", "-p", "-f", str(cache), "-P", passphrase, "-N", ""],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
        return str(cache)

    def _open_tunnel(self, ssh_host: str, env: dict) -> None:
        ssh_user = env.get("DB_SSH_USER", "")
        ssh_key = env.get("DB_SSH_KEY", "")
        if not (ssh_user and ssh_key):
            raise RuntimeError(
                f"DB_SSH_HOST is set for account '{self.account}' but DB_SSH_USER/DB_SSH_KEY "
                "are missing in config/db/.env"
            )
        ssh_port = int(env.get("DB_SSH_PORT") or 22)
        ssh_key_path = self._prepare_key(
            str(Path(ssh_key).expanduser()), env.get("DB_SSH_KEY_PASSPHRASE") or None
        )
        # The DB host/port as seen FROM the bastion — usually a private/in-cluster
        # address, distinct from DB_HOST which becomes 127.0.0.1 once tunneled.
        remote_host = env.get("DB_REMOTE_HOST") or self._connect_host
        remote_port = int(env.get("DB_REMOTE_PORT") or self._connect_port)
        local_port = _free_local_port()

        cmd = [
            "ssh", "-i", ssh_key_path, "-p", str(ssh_port), "-N",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30",
            "-L", f"{local_port}:{remote_host}:{remote_port}",
            f"{ssh_user}@{ssh_host}",
        ]
        self._tunnel_proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
        )
        try:
            _wait_for_port("127.0.0.1", local_port)
        except RuntimeError:
            stderr = ""
            if self._tunnel_proc.poll() is not None:
                stderr = (self._tunnel_proc.stderr.read() or "").strip()
            self._terminate_tunnel()
            raise RuntimeError(
                f"SSH tunnel to '{ssh_host}' (account '{self.account}') failed to come up"
                + (f": {stderr}" if stderr else "")
            )
        self._connect_host = "127.0.0.1"
        self._connect_port = local_port

    def _terminate_tunnel(self) -> None:
        if self._tunnel_proc is None:
            return
        if self._tunnel_proc.poll() is None:
            self._tunnel_proc.terminate()
            try:
                self._tunnel_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._tunnel_proc.kill()
        self._tunnel_proc = None

    def _connection(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                host=self._connect_host,
                port=self._connect_port,
                dbname=self.db_name,
                user=self._db_user,
                password=self._db_password,
                connect_timeout=10,
            )
            self._conn.autocommit = True
            with self._conn.cursor() as cur:
                cur.execute("SET default_transaction_read_only = on")
                cur.execute(f"SET statement_timeout = {self._statement_timeout_ms}")
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._terminate_tunnel()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def health(self) -> dict:
        with self._connection().cursor() as cur:
            cur.execute("SELECT version(), now()")
            version, now = cur.fetchone()
        return {
            "account": self.account,
            "db_name": self.db_name,
            "host": self._connect_host,
            "port": self._connect_port,
            "tunneled": self._tunnel_proc is not None,
            "version": version,
            "server_time": str(now),
        }

    def query(self, sql: str, params: Optional[tuple] = None, limit: Optional[int] = None) -> dict:
        """
        Run a read-only query (guarded by `assert_read_only`). Returns
        {"columns": [...], "rows": [(...), ...], "truncated": bool}.

        `limit` caps rows actually fetched client-side (does not rewrite the
        SQL) — defaults to DB_ROW_LIMIT from config/db/.env.
        """
        assert_read_only(sql)
        cap = limit or self._default_row_limit
        with self._connection().cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return {"columns": [], "rows": [], "truncated": False}
            columns = [d.name for d in cur.description]
            rows = cur.fetchmany(cap + 1)
        truncated = len(rows) > cap
        if truncated:
            rows = rows[:cap]
        return {"columns": columns, "rows": rows, "truncated": truncated}

    def list_tables(self, schema: str = "public") -> list:
        result = self.query(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s ORDER BY table_name",
            params=(schema,),
            limit=1000,
        )
        return [row[0] for row in result["rows"]]

    def describe_table(self, table: str, schema: str = "public") -> dict:
        return self.query(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s "
            "ORDER BY ordinal_position",
            params=(schema, table),
            limit=500,
        )
