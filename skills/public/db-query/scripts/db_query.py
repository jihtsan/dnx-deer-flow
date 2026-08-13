#!/usr/bin/env python3
"""db_query.py - Read-only database query client.

Loads database aliases from a server-owned environment file and connects
directly to the selected database.

Usage:
  python db_query.py list_aliases
  python db_query.py list_tables <alias>
  python db_query.py search_tables <alias> <keyword>
  python db_query.py search_columns <alias> <keyword>
  python db_query.py describe_table <alias> <table>
  python db_query.py sample_data <alias> <table> [limit]
  python db_query.py query <alias> "<SELECT ...>"

Env vars:
  DB_ENV_FILE      Path to .env file for direct fallback (default: ~/.db-readonly.env)
  DB_ALIAS_PREFIXES  Comma-separated alias prefixes when config is environment-only
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Env loading (used only in fallback / direct mode)
# ---------------------------------------------------------------------------


def _load_env(override: bool = False) -> None:
    env_file = _env_file_path()
    if not env_file.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_file, override=override)
    except ImportError:
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                if key and (override or key not in os.environ):
                    os.environ[key] = val


# ---------------------------------------------------------------------------
# Alias discovery (direct mode)
# ---------------------------------------------------------------------------

_ALIAS_RE = re.compile(r"^([A-Z0-9_]+)_TYPE$")


def _env_file_path() -> Path:
    configured = os.getenv("DB_ENV_FILE", "~/.db-readonly.env")
    return Path(configured).expanduser()


def _configured_alias_prefixes() -> list[str]:
    prefixes = {item.strip().upper() for item in os.getenv("DB_ALIAS_PREFIXES", "").split(",") if item.strip()}
    env_file = _env_file_path()
    if env_file.exists():
        with env_file.open(encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key = line.partition("=")[0].strip()
                if match := _ALIAS_RE.match(key):
                    prefixes.add(match.group(1))
    return sorted(prefixes)


def _discover_aliases() -> dict[str, dict]:
    aliases: dict[str, dict] = {}
    for prefix in _configured_alias_prefixes():
        db_type = os.getenv(f"{prefix}_TYPE", "").strip().lower()
        if db_type not in ("mysql", "pg"):
            continue
        alias = prefix.lower()
        try:
            config = _read_config(prefix)
            config["type"] = db_type
            aliases[alias] = config
        except KeyError as e:
            _warn(f"Skipping alias '{alias}': missing env var {e}")
    return aliases


def _read_config(prefix: str) -> dict:
    def _get(suffix: str) -> str:
        key = f"{prefix}_{suffix}"
        val = os.environ.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def _get_optional(suffix: str) -> str | None:
        key = f"{prefix}_{suffix}"
        val = os.environ.get(key)
        if val is None:
            return None
        val = val.strip()
        return val or None

    project = _get_optional("PROJECT")
    return {
        "host": _get("HOST"),
        "port": int(_get("PORT")),
        "user": _get("USER"),
        "password": _get("PASSWORD"),
        "database": _get("DATABASE"),
        "project": [item.strip() for item in project.split(",")] if project else None,
        "desc": _get_optional("DESC"),
    }


def _alias_payload(alias: str, cfg: dict) -> dict:
    payload = {
        "alias": alias,
        "type": cfg["type"],
        "host": cfg["host"],
        "database": cfg["database"],
    }
    if cfg.get("project"):
        payload["project"] = cfg["project"]
    if cfg.get("desc"):
        payload["desc"] = cfg["desc"]
    return payload


# ---------------------------------------------------------------------------
# Direct DB operations (fallback)
# ---------------------------------------------------------------------------


def _mysql_conn(cfg: dict):
    import pymysql

    connection_args = {
        "host": cfg["host"],
        "port": cfg["port"],
        "user": cfg["user"],
        "password": cfg["password"],
        "database": cfg["database"],
        "charset": "utf8mb4",
        "connect_timeout": 5,
        "cursorclass": pymysql.cursors.DictCursor,
    }
    return pymysql.connect(**connection_args)


def _pg_conn(cfg: dict):
    import psycopg2
    import psycopg2.extras

    connection_args = {
        "host": cfg["host"],
        "port": cfg["port"],
        "user": cfg["user"],
        "password": cfg["password"],
        "dbname": cfg["database"],
        "connect_timeout": 5,
        "cursor_factory": psycopg2.extras.RealDictCursor,
    }
    conn = psycopg2.connect(**connection_args)
    conn.set_session(readonly=True)
    return conn


def _run_op(cfg: dict, op: str, **kwargs):
    t = cfg["type"]
    if t == "mysql":
        conn = _mysql_conn(cfg)

        def _q(sql):
            with conn.cursor() as cur:
                cur.execute("SET SESSION MAX_EXECUTION_TIME=30000")
                cur.execute(sql)
                return [_ser(r) for r in cur.fetchall()]

        def _tables():
            with conn.cursor() as cur:
                cur.execute("SHOW TABLES")
                return [list(r.values())[0] for r in cur.fetchall()]

        def _search_tables(keyword):
            kw = f"%{keyword}%"
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT table_schema, table_name
                       FROM information_schema.tables
                       WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
                         AND table_name LIKE %s
                       ORDER BY CASE WHEN table_schema = %s THEN 0 ELSE 1 END,
                                table_schema, table_name
                       LIMIT 200""",
                    (kw, cfg["database"]),
                )
                rows = cur.fetchall()
                return [row["table_name"] if row["table_schema"] == cfg["database"] else f"{row['table_schema']}.{row['table_name']}" for row in rows]

        def _search_columns(keyword):
            kw = f"%{keyword}%"
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT table_schema, table_name, column_name,
                              COLUMN_TYPE AS column_type
                       FROM information_schema.columns
                       WHERE table_schema NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
                         AND (column_name LIKE %s OR table_name LIKE %s)
                       ORDER BY CASE WHEN table_schema = %s THEN 0 ELSE 1 END,
                                table_schema, table_name, ordinal_position
                       LIMIT 200""",
                    (kw, kw, cfg["database"]),
                )
                rows = cur.fetchall()
                return [
                    {
                        "table": row["table_name"] if row["table_schema"] == cfg["database"] else f"{row['table_schema']}.{row['table_name']}",
                        "column_name": row["column_name"],
                        "column_type": row["column_type"],
                    }
                    for row in rows
                ]

        def _describe(table):
            schema, table_name = _parse_table_ref(table, default_schema=cfg["database"])
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT COLUMN_NAME AS column_name, COLUMN_TYPE AS column_type,
                              IS_NULLABLE AS is_nullable, COLUMN_DEFAULT AS column_default,
                              COLUMN_COMMENT AS column_comment, COLUMN_KEY AS column_key,
                              EXTRA AS extra
                       FROM information_schema.COLUMNS
                       WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
                       ORDER BY ORDINAL_POSITION""",
                    (schema, table_name),
                )
                return list(cur.fetchall())

        def _sample(table, limit):
            schema, table_name = _parse_table_ref(table, default_schema=cfg["database"])
            limit = max(1, min(limit, 100))
            with conn.cursor() as cur:
                cur.execute(f"SELECT * FROM `{schema}`.`{table_name}` LIMIT %s", (limit,))
                return [_ser(r) for r in cur.fetchall()]
    else:
        conn = _pg_conn(cfg)

        def _q(sql):
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout='30s'")
                cur.execute(sql)
                return [_ser(dict(r)) for r in cur.fetchall()]

        def _tables():
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT schemaname, tablename
                       FROM pg_tables
                       WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
                       ORDER BY schemaname, tablename"""
                )
                return [r["tablename"] if r["schemaname"] == "public" else f"{r['schemaname']}.{r['tablename']}" for r in cur.fetchall()]

        def _search_tables(keyword):
            kw = f"%{keyword}%"
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT schemaname, tablename
                       FROM pg_tables
                       WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
                         AND tablename ILIKE %s
                       ORDER BY CASE WHEN schemaname = 'public' THEN 0 ELSE 1 END,
                                schemaname, tablename
                       LIMIT 200""",
                    (kw,),
                )
                return [row["tablename"] if row["schemaname"] == "public" else f"{row['schemaname']}.{row['tablename']}" for row in cur.fetchall()]

        def _search_columns(keyword):
            kw = f"%{keyword}%"
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT table_schema, table_name, column_name, data_type
                       FROM information_schema.columns
                       WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                         AND (column_name ILIKE %s OR table_name ILIKE %s)
                       ORDER BY CASE WHEN table_schema = 'public' THEN 0 ELSE 1 END,
                                table_schema, table_name, ordinal_position
                       LIMIT 200""",
                    (kw, kw),
                )
                return [
                    {
                        "table": row["table_name"] if row["table_schema"] == "public" else f"{row['table_schema']}.{row['table_name']}",
                        "column_name": row["column_name"],
                        "column_type": row["data_type"],
                    }
                    for row in cur.fetchall()
                ]

        def _describe(table):
            schema, table_name = _parse_table_ref(table, default_schema="public")
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT column_name, data_type AS column_type,
                              is_nullable, column_default, '' AS column_comment
                       FROM information_schema.columns
                       WHERE table_schema=%s AND table_name=%s
                       ORDER BY ordinal_position""",
                    (schema, table_name),
                )
                return [dict(r) for r in cur.fetchall()]

        def _sample(table, limit):
            schema, table_name = _parse_table_ref(table, default_schema="public")
            limit = max(1, min(limit, 100))
            with conn.cursor() as cur:
                cur.execute(f'SELECT * FROM "{schema}"."{table_name}" LIMIT %s', (limit,))
                return [_ser(dict(r)) for r in cur.fetchall()]

    try:
        if op == "list_tables":
            return _tables()
        elif op == "search_tables":
            return _search_tables(kwargs["keyword"])
        elif op == "search_columns":
            return _search_columns(kwargs["keyword"])
        elif op == "describe_table":
            return _describe(kwargs["table"])
        elif op == "sample_data":
            return _sample(kwargs["table"], kwargs.get("limit", 10))
        elif op == "query":
            _assert_select(kwargs["sql"])
            return _q(kwargs["sql"])
    finally:
        conn.close()


def _execute(direct_fn) -> dict:
    _load_env()
    try:
        return direct_fn()
    except Exception as e:
        msg = _redact(str(e))
        return {"status": "error", "message": msg}


def _get_alias_direct(aliases: dict, alias: str) -> dict | None:
    alias = alias.lower()
    if alias not in aliases:
        return None
    return aliases[alias]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_list_aliases() -> None:
    result = _execute(lambda: {"status": "ok", "data": [_alias_payload(a, c) for a, c in _discover_aliases().items()]})
    _print(result)


def cmd_list_tables(alias: str) -> None:
    result = _execute(lambda: _direct_op(alias, "list_tables"))
    _print(result)


def cmd_search_tables(alias: str, keyword: str) -> None:
    result = _execute(lambda: _direct_op(alias, "search_tables", keyword=keyword))
    _print(result)


def cmd_search_columns(alias: str, keyword: str) -> None:
    result = _execute(lambda: _direct_op(alias, "search_columns", keyword=keyword))
    _print(result)


def cmd_describe_table(alias: str, table: str) -> None:
    result = _execute(lambda: _direct_op(alias, "describe_table", table=table))
    _print(result)


def cmd_sample_data(alias: str, table: str, limit: int = 10) -> None:
    result = _execute(lambda: _direct_op(alias, "sample_data", table=table, limit=limit))
    _print(result)


def cmd_query(alias: str, sql: str) -> None:
    result = _execute(lambda: _direct_op(alias, "query", sql=sql))
    _print(result)


def _direct_op(alias: str, op: str, **kwargs) -> dict:
    aliases = _discover_aliases()
    alias = alias.lower()
    if alias not in aliases:
        available = ", ".join(aliases) or "(none)"
        return {"status": "error", "message": f"Unknown alias '{alias}'. Available: {available}"}
    try:
        data = _run_op(aliases[alias], op, **kwargs)
        return {"status": "ok", "data": data}
    except ValueError as e:
        return {"status": "error", "message": str(e)}
    except Exception as e:
        return {"status": "error", "message": _redact(str(e))}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAFE_ID = re.compile(r"^[A-Za-z0-9_]+$")
_SAFE_TABLE_REF = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?$")
_FORBIDDEN_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|REPLACE|MERGE|UPSERT|CREATE|ALTER|DROP|TRUNCATE|"
    r"GRANT|REVOKE|CALL|EXEC|EXECUTE|COPY|VACUUM|ANALYZE|COMMENT|ATTACH|DETACH|"
    r"BEGIN|COMMIT|ROLLBACK|SAVEPOINT)\b",
    flags=re.IGNORECASE,
)


def _validate_id(name: str) -> None:
    if not _SAFE_ID.match(name):
        raise ValueError(f"Invalid identifier: {name!r}")


def _parse_table_ref(name: str, default_schema: str) -> tuple[str, str]:
    if not _SAFE_TABLE_REF.match(name):
        raise ValueError(f"Invalid identifier: {name!r}")
    parts = name.split(".", 1)
    if len(parts) == 1:
        return default_schema, parts[0]
    return parts[0], parts[1]


def _strip_sql_lead(sql: str) -> str:
    s = sql.lstrip()
    while True:
        if s.startswith("--"):
            line_end = s.find("\n")
            if line_end == -1:
                return ""
            s = s[line_end + 1 :].lstrip()
            continue
        if s.startswith("/*"):
            block_end = s.find("*/")
            if block_end == -1:
                return ""
            s = s[block_end + 2 :].lstrip()
            continue
        if s.startswith("("):
            s = s[1:].lstrip()
            continue
        return s


def _strip_sql_comments_and_literals(sql: str) -> str:
    result: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":
            i += 2
            while i < n and sql[i] != "\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            while i < n:
                if sql[i] == quote:
                    if quote == "'" and i + 1 < n and sql[i + 1] == quote:
                        i += 2
                        continue
                    i += 1
                    break
                if sql[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                i += 1
            result.append(" ")
            continue
        result.append(ch)
        i += 1
    return "".join(result)


def _assert_select(sql: str) -> None:
    lead = _strip_sql_lead(sql).upper()
    if not (lead.startswith("SELECT") or lead.startswith("WITH")):
        raise ValueError("Only SELECT statements are allowed.")
    cleaned = _strip_sql_comments_and_literals(sql)
    cleaned = re.sub(r";\s*$", "", cleaned.strip())
    if ";" in cleaned:
        raise ValueError("Only a single read-only statement is allowed.")
    if _FORBIDDEN_SQL_RE.search(cleaned):
        raise ValueError("Only read-only SELECT statements are allowed.")


def _ser(row: dict) -> dict:
    import datetime
    from decimal import Decimal

    out = {}
    for k, v in row.items():
        if isinstance(v, bytes):
            out[k] = v.decode("utf-8", errors="replace")
        elif isinstance(v, (Decimal, datetime.date, datetime.datetime, datetime.time)):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def _redact(text: str) -> str:
    return re.sub(r"(password\s*[=:]\s*)\S+", r"\1***", text, flags=re.IGNORECASE)


def _print(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=False, default=str))
    if result.get("status") == "error":
        sys.exit(1)


def _warn(msg: str) -> None:
    print(f"[db_query] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        sys.exit(1)

    cmd, rest = args[0], args[1:]

    if cmd == "list_aliases":
        cmd_list_aliases()

    elif cmd == "list_tables":
        if not rest:
            print("Usage: list_tables <alias>")
            sys.exit(1)
        cmd_list_tables(rest[0])

    elif cmd == "search_tables":
        if len(rest) < 2:
            print("Usage: search_tables <alias> <keyword>")
            sys.exit(1)
        cmd_search_tables(rest[0], rest[1])

    elif cmd == "search_columns":
        if len(rest) < 2:
            print("Usage: search_columns <alias> <keyword>")
            sys.exit(1)
        cmd_search_columns(rest[0], rest[1])

    elif cmd == "describe_table":
        if len(rest) < 2:
            print("Usage: describe_table <alias> <table>")
            sys.exit(1)
        cmd_describe_table(rest[0], rest[1])

    elif cmd == "sample_data":
        if len(rest) < 2:
            print("Usage: sample_data <alias> <table> [limit]")
            sys.exit(1)
        limit = int(rest[2]) if len(rest) > 2 else 10
        cmd_sample_data(rest[0], rest[1], limit)

    elif cmd == "query":
        if len(rest) < 2:
            print('Usage: query <alias> "<SELECT ...>"')
            sys.exit(1)
        cmd_query(rest[0], rest[1])

    else:
        print(f"Unknown command: {cmd!r}. Commands: list_aliases, list_tables, search_tables, search_columns, describe_table, sample_data, query")
        sys.exit(1)


if __name__ == "__main__":
    main()
