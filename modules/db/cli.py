"""
DB (Postgres, read-only) CLI commands. Called from cli/agent.py dispatcher.
run_command() returns True if the command was handled, False otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

_WORKFLOW_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_WORKFLOW_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKFLOW_ROOT))

_db_clients: dict = {}
_active_account = "default"

_KNOWN_FLAGS = {"account", "limit", "schema"}


def _get_db(account: str | None = None):
    """Return a DBClient for `account` (or the active one), cached per account —
    keeps a tunnel/connection alive across commands in interactive mode."""
    acc = account or _active_account
    if acc not in _db_clients:
        from modules.db.client import DBClient
        _db_clients[acc] = DBClient(account=acc)
    return _db_clients[acc]


def _resolve_active_account(parts: list) -> str:
    """Account for this DB command: explicit --account=, else WORKFLOW_ACCOUNT / default."""
    from shared.config_loader import resolve_account
    explicit = next((p.split("=", 1)[1] for p in parts if p.startswith("--account=")), None)
    if explicit:
        return explicit
    return resolve_account("db")


def _parse_kv_flags(parts: list) -> dict:
    """Only pulls out known --key=value flags — SQL comments also start with
    `--`, so an unrecognized `--...` token is left as part of the query."""
    flags: dict = {}
    for part in parts:
        if part.startswith("--") and "=" in part:
            key, value = part[2:].split("=", 1)
            if key in _KNOWN_FLAGS:
                flags[key] = value
    return flags


def _split_query_and_flags(parts: list) -> tuple:
    query_parts, flags = [], {}
    for part in parts:
        if part.startswith("--") and "=" in part and part[2:].split("=", 1)[0] in _KNOWN_FLAGS:
            key, value = part[2:].split("=", 1)
            flags[key] = value
        else:
            query_parts.append(part)
    return " ".join(query_parts), flags


# ------------------------------------------------------------------
# Formatting
# ------------------------------------------------------------------

_MAX_CELL_CHARS = 60


def fmt_table(columns: list, rows: list) -> str:
    if not rows:
        return "  (нет строк)"

    def cell(v: object) -> str:
        s = "∅" if v is None else str(v)
        return s if len(s) <= _MAX_CELL_CHARS else s[: _MAX_CELL_CHARS - 1] + "…"

    str_rows = [[cell(v) for v in row] for row in rows]
    widths = [len(c) for c in columns]
    for row in str_rows:
        for i, v in enumerate(row):
            widths[i] = max(widths[i], len(v))

    def fmt_row(vals: list) -> str:
        return "  " + " | ".join(v.ljust(widths[i]) for i, v in enumerate(vals))

    lines = [fmt_row(list(columns)), "  " + "-+-".join("-" * w for w in widths)]
    lines.extend(fmt_row(row) for row in str_rows)
    return "\n".join(lines)


# ------------------------------------------------------------------
# Command implementations
# ------------------------------------------------------------------

def cmd_db_info():
    health = _get_db().health()
    tunnel_note = " (через SSH-туннель)" if health["tunneled"] else ""
    print(f"\n  DB: {health['db_name']}@{health['host']}:{health['port']}{tunnel_note}  [account={health['account']}]")
    print(f"  Version: {health['version']}")
    print(f"  Server time: {health['server_time']}\n")


def cmd_db_tables(schema: str = "public"):
    tables = _get_db().list_tables(schema=schema)
    print(f"\n  Таблицы схемы '{schema}' ({len(tables)}):\n")
    if not tables:
        print("  (нет)\n")
        return
    for t in tables:
        print(f"  {t}")
    print()


def cmd_db_describe(table: str, schema: str = "public"):
    result = _get_db().describe_table(table, schema=schema)
    print(f"\n  Колонки {schema}.{table}:\n")
    print(fmt_table(result["columns"], result["rows"]))
    print()


def cmd_db_query(sql: str, limit: int = 200):
    result = _get_db().query(sql, limit=limit)
    print(f"\n  Query: {sql}\n")
    print(fmt_table(result["columns"], result["rows"]))
    print(f"\n  {len(result['rows'])} строк.")
    if result["truncated"]:
        print(f"  ⚠ вернулось ровно limit={limit} строк — результат мог быть обрезан, сузь запрос или увеличь --limit.")
    print()


# ------------------------------------------------------------------
# Command router
# ------------------------------------------------------------------

DB_COMMANDS = {"db-info", "db-tables", "db-describe", "db-query"}


def run_command(cmd: str, parts: list) -> bool:
    """Route a DB command. Returns True if handled, False otherwise."""
    if cmd not in DB_COMMANDS:
        return False

    global _active_account
    _active_account = _resolve_active_account(parts)

    try:
        if cmd == "db-info":
            cmd_db_info()

        elif cmd == "db-tables":
            flags = _parse_kv_flags(parts[1:])
            cmd_db_tables(schema=flags.get("schema", "public"))

        elif cmd == "db-describe":
            if len(parts) < 2:
                print("  Использование: db-describe <table> [--schema=public]")
            else:
                flags = _parse_kv_flags(parts[1:])
                cmd_db_describe(parts[1], schema=flags.get("schema", "public"))

        elif cmd == "db-query":
            if len(parts) < 2:
                print('  Использование: db-query "<SELECT ...>" [--limit=200] [--account=]')
            else:
                query, flags = _split_query_and_flags(parts[1:])
                cmd_db_query(query, limit=int(flags.get("limit", 200)))

    except Exception as e:
        print(f"  Ошибка: {e}\n")

    return True
