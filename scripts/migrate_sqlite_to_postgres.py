import argparse
from collections import defaultdict
from collections import Counter
from datetime import date, datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.migration_errors import PreflightError


DEFAULT_SQLITE_PATH = ROOT / "instance" / "palavra-do-dia.sqlite3"
POSTGRES_MIGRATIONS_PATH = ROOT / "migrations" / "postgresql"
CONTROL_TABLES = {"schema_migrations"}
POSTGRES_TYPES = {
    "BIGINT": "bigint",
    "INTEGER": "integer",
    "TEXT": "text",
    "DATE": "date",
    "TIMESTAMPTZ": "timestamp with time zone",
}
SQLITE_TYPE_COMPATIBILITY = {
    "bigint": {"INTEGER"},
    "integer": {"INTEGER"},
    "text": {"TEXT"},
    "date": {"TEXT"},
    "timestamp with time zone": {"TEXT"},
}


def _quote_identifier(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def _format_preflight_diagnostic(error):
    details = (
        " details="
        + json.dumps(
            error.diagnostic_details,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if error.diagnostic_details
        else ""
    )
    return f"code={error.diagnostic_code}{details}"


def _split_sql_items(body):
    items = []
    start = depth = 0
    quoted = False
    index = 0
    while index < len(body):
        char = body[index]
        if char == "'":
            if quoted and index + 1 < len(body) and body[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        elif not quoted:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                items.append(body[start:index].strip())
                start = index + 1
        index += 1
    if body[start:].strip():
        items.append(body[start:].strip())
    return items


def _normalize_default(value):
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value.strip()).lower()
    if re.fullmatch(
        r"\(?\s*strftime\('%y-%m-%dt%h:%m:%fz',\s*'now'\)\s*\)?",
        normalized,
    ):
        return "current_timestamp"
    if normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    return normalized


def _extract_check_expressions(sql):
    expressions = []
    position = 0
    while position < len(sql):
        if sql[position] == "'":
            position += 1
            while position < len(sql):
                if sql[position] == "'":
                    if position + 1 < len(sql) and sql[position + 1] == "'":
                        position += 2
                        continue
                    position += 1
                    break
                position += 1
            continue
        if (
            sql[position : position + 5].upper() == "CHECK"
            and (position == 0 or not (sql[position - 1].isalnum() or sql[position - 1] == "_"))
            and (
                position + 5 == len(sql)
                or not (sql[position + 5].isalnum() or sql[position + 5] == "_")
            )
        ):
            opening = position + 5
            while opening < len(sql) and sql[opening].isspace():
                opening += 1
            if opening >= len(sql) or sql[opening] != "(":
                position += 5
                continue
        else:
            position += 1
            continue

        depth = 1
        index = opening + 1
        while index < len(sql) and depth:
            char = sql[index]
            if char == "'":
                index += 1
                while index < len(sql):
                    if sql[index] == "'":
                        if index + 1 < len(sql) and sql[index + 1] == "'":
                            index += 2
                            continue
                        index += 1
                        break
                    index += 1
                continue
            else:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
            index += 1
        if depth:
            raise PreflightError("CHECK constraint sem fechamento de parênteses.")
        expressions.append(
            _normalize_check_expression(sql[opening + 1 : index - 1])
        )
        position = index
    return expressions


def _normalize_check_expression(expression):
    quoted_values = []
    outside_literals = []
    placeholder_prefix = "__check_literal_"
    while placeholder_prefix.lower() in expression.lower():
        placeholder_prefix += "_"
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == "'":
            literal = [char]
            index += 1
            while index < len(expression):
                literal.append(expression[index])
                if expression[index] == "'":
                    if index + 1 < len(expression) and expression[index + 1] == "'":
                        literal.append("'")
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            placeholder = f"{placeholder_prefix}{len(quoted_values)}__"
            quoted_values.append("".join(literal))
            outside_literals.append(placeholder)
            continue
        outside_literals.append(char)
        index += 1
    normalized = "".join(outside_literals).lower()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"\s*([(),])\s*", r"\1", normalized)
    normalized = re.sub(
        r"\s*(<=|>=|<>|!=|=|<|>)\s*",
        r" \1 ",
        normalized,
    )
    normalized = re.sub(
        r"\b(and|or|in)\(",
        r"\1 (",
        normalized,
    )
    normalized = re.sub(
        r"\)(and|or|in)\b",
        r") \1",
        normalized,
    )
    normalized = normalized.strip()
    for index, literal in enumerate(quoted_values):
        normalized = normalized.replace(
            f"{placeholder_prefix}{index}__",
            literal,
        )
    return normalized


_DATE_GLOB_EXPRESSION = (
    "glob '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'"
)


def _type_substitution(table, source_expression, target_checks, target_columns):
    for column, metadata in target_columns.items():
        if (
            metadata["type"] == "date"
            and source_expression == f"{column} {_DATE_GLOB_EXPRESSION}"
        ):
            return {
                "classification": "stronger_postgresql_type",
                "table": table,
                "column": column,
                "postgres_type": "date",
                "sqlite_check": source_expression,
                "postgres_check": None,
                "explanation": (
                    "PostgreSQL DATE enforces a real calendar date; preflight "
                    "also validates the source value is canonical YYYY-MM-DD."
                ),
            }
        if (
            metadata["type"] == "date"
            and source_expression == f"length(trim({column})) > 0"
        ):
            return {
                "classification": "stronger_postgresql_type",
                "table": table,
                "column": column,
                "postgres_type": "date",
                "sqlite_check": source_expression,
                "postgres_check": None,
                "explanation": (
                    "A non-NULL PostgreSQL DATE cannot contain an empty string; "
                    "preflight also validates the source value is canonical YYYY-MM-DD."
                ),
            }
        if (
            metadata["type"] == "timestamp with time zone"
            and column == "published_at"
            and table == "reflections"
        ):
            reduced = re.sub(
                r"\s+and\s+length\(trim\(published_at\)\)\s*>\s*0(?=\s*\))",
                "",
                source_expression,
            )
            matching_target = next(
                (
                    target_expression
                    for target_expression in target_checks
                    if target_expression == reduced
                ),
                None,
            )
            if matching_target is not None:
                return {
                    "classification": "stronger_postgresql_type",
                    "table": table,
                    "column": column,
                    "postgres_type": "timestamp with time zone",
                    "sqlite_check": source_expression,
                    "postgres_check": matching_target,
                    "explanation": (
                        "TIMESTAMPTZ replaces the non-empty text condition by "
                        "requiring a valid timestamp; preflight verifies a "
                        "timezone-aware ISO timestamp."
                    ),
                }
    return None


def _compare_check_constraints(sqlite_tables, postgres_tables):
    substitutions = []
    unresolved = []
    exact_matches = 0
    for table, source in sqlite_tables.items():
        source_checks = Counter(source["check_constraints"])
        target_checks = Counter(postgres_tables[table]["check_constraints"])
        exact_matches += sum((source_checks & target_checks).values())
        source_only = list((source_checks - target_checks).elements())
        target_only = list((target_checks - source_checks).elements())
        remaining_target = list(target_only)
        remaining_source = []
        for expression in source_only:
            substitution = _type_substitution(
                table,
                expression,
                remaining_target,
                postgres_tables[table]["columns"],
            )
            if substitution is None:
                remaining_source.append(expression)
                continue
            substitutions.append(substitution)
            if substitution["postgres_check"] is not None:
                remaining_target.remove(substitution["postgres_check"])
        if remaining_source or remaining_target:
            unresolved.append(
                {
                    "table": table,
                    "sqlite_only": sorted(remaining_source),
                    "postgresql_only": sorted(remaining_target),
                    "classification": "unresolved_semantic_difference",
                }
            )
    return {
        "exact_matches": exact_matches,
        "textual_differences": substitutions,
        "type_substitutions": substitutions,
        "unresolved_differences": unresolved,
    }


def _validate_postgres_typed_values(connection, postgres_tables):
    validated_columns = 0
    validated_values = 0
    for table, metadata in postgres_tables.items():
        for column, column_metadata in metadata["columns"].items():
            target_type = column_metadata["type"]
            if target_type not in {"date", "timestamp with time zone"}:
                continue
            validated_columns += 1
            quoted_table = _quote_identifier(table)
            quoted_column = _quote_identifier(column)
            values = connection.execute(
                f"SELECT {quoted_column} FROM {quoted_table}"
            )
            for row_number, row in enumerate(values, start=1):
                value = row[0]
                validated_values += 1
                if value is None:
                    if column_metadata["nullable"]:
                        continue
                    raise PreflightError(
                        f"Valor NULL incompatível com PostgreSQL {target_type} "
                        f"em {table}.{column}, linha {row_number}."
                    )
                if target_type == "date":
                    valid = (
                        isinstance(value, str)
                        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None
                    )
                    if valid:
                        try:
                            valid = date.fromisoformat(value).isoformat() == value
                        except ValueError:
                            valid = False
                    expected_format = "valid DATE in canonical YYYY-MM-DD format"
                else:
                    timestamp_match = (
                        re.fullmatch(
                            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
                            r"(?:\.\d{1,6})?(?:Z|([+-])(\d{2}):(\d{2}))",
                            value,
                        )
                        if isinstance(value, str)
                        else None
                    )
                    valid = timestamp_match is not None
                    if valid and timestamp_match.group(2) is not None:
                        valid = (
                            int(timestamp_match.group(2)) <= 15
                            and int(timestamp_match.group(3)) <= 59
                        )
                    if valid:
                        try:
                            parsed = datetime.fromisoformat(
                                value[:-1] + "+00:00"
                                if value.endswith("Z")
                                else value
                            )
                            valid = (
                                parsed.tzinfo is not None
                                and parsed.utcoffset() is not None
                            )
                        except ValueError:
                            valid = False
                    expected_format = (
                        "valid timezone-aware ISO timestamp for TIMESTAMPTZ"
                    )
                if not valid:
                    raise PreflightError(
                        f"Valor incompatível com PostgreSQL {target_type} "
                        f"em {table}.{column}, linha {row_number}: "
                        f"esperado {expected_format}."
                    )
    return {
        "validated_columns": validated_columns,
        "validated_values": validated_values,
        "incompatible_values": 0,
    }


def _identity_divergences(sqlite_tables, postgres_tables):
    divergences = []
    for table, source in sqlite_tables.items():
        target = postgres_tables[table]
        source_foreign_key_columns = {
            column
            for columns, _ref_table, _ref_columns, _action in source["foreign_keys"]
            for column in columns
        }
        single_integer_primary_key = (
            len(source["primary_key"]) == 1
            and source["columns"][source["primary_key"][0]]["type"] == "INTEGER"
        )
        for column, source_column in source["columns"].items():
            expected = None
            if (
                single_integer_primary_key
                and source["primary_key"][0] == column
                and column not in source_foreign_key_columns
            ):
                expected = "BY DEFAULT"
            actual = target["columns"][column]["identity"]
            if expected != actual:
                divergences.append(
                    {
                        "kind": "identity",
                        "table": table,
                        "column": column,
                        "expected_generation": expected,
                        "actual_generation": actual,
                    }
                )
    return divergences


def _parse_postgres_migrations(migrations_path):
    files = sorted(Path(migrations_path).glob("[0-9][0-9][0-9]_*.sql"))
    if len(files) != 8:
        raise PreflightError(
            f"Esperadas oito migrações PostgreSQL; encontradas {len(files)}."
        )

    tables = {}
    indexes = {}
    for migration_file in files:
        sql = re.sub(
            r"(?m)^\s*--.*$",
            "",
            migration_file.read_text(encoding="utf-8"),
        )
        for match in re.finditer(
            r"CREATE TABLE (\w+)\s*\((.*?)\n\);",
            sql,
            re.S,
        ):
            table, body = match.groups()
            columns = {}
            primary_key = []
            unique_keys = set()
            foreign_keys = []
            check_constraints = []
            for item in _split_sql_items(body):
                check_constraints.extend(_extract_check_expressions(item))
                primary = re.match(
                    r"(?is)^(?:CONSTRAINT\s+\w+\s+)?PRIMARY\s+KEY\s*"
                    r"\(([^)]+)\)",
                    item,
                )
                if primary:
                    primary_key = [
                        column.strip()
                        for column in primary.group(1).split(",")
                    ]
                    continue
                unique = re.match(
                    r"(?is)^(?:CONSTRAINT\s+\w+\s+)?UNIQUE\s*"
                    r"\(([^)]+)\)",
                    item,
                )
                if unique:
                    unique_keys.add(
                        tuple(
                            column.strip()
                            for column in unique.group(1).split(",")
                        )
                    )
                    continue
                table_fk = re.match(
                    r"(?is)^(?:CONSTRAINT\s+\w+\s+)?FOREIGN\s+KEY\s*"
                    r"\(([^)]+)\)\s*REFERENCES\s+(\w+)\s*"
                    r"\(([^)]+)\)(.*)$",
                    item,
                )
                if table_fk:
                    source_columns = tuple(
                        column.strip()
                        for column in table_fk.group(1).split(",")
                    )
                    reference_columns = tuple(
                        column.strip()
                        for column in table_fk.group(3).split(",")
                    )
                    on_delete = re.search(
                        r"\bON\s+DELETE\s+(RESTRICT|CASCADE|SET NULL|SET DEFAULT|NO ACTION)\b",
                        table_fk.group(4),
                        re.I,
                    )
                    foreign_keys.append(
                        (
                            source_columns,
                            table_fk.group(2),
                            reference_columns,
                            on_delete.group(1).upper()
                            if on_delete
                            else "NO ACTION",
                        )
                    )
                    continue
                if re.match(
                    r"(?is)^(?:CONSTRAINT\s+\w+\s+)?CHECK\s*\(",
                    item,
                ):
                    continue

                column = re.match(
                    r"(?is)^(\w+)\s+([A-Z]+)\b(.*)$",
                    item,
                )
                if not column:
                    raise PreflightError(
                        f"Não foi possível interpretar definição de "
                        f"{migration_file.name}: {item}"
                    )
                name, type_name, remainder = column.groups()
                if type_name.upper() not in POSTGRES_TYPES:
                    raise PreflightError(
                        f"Tipo PostgreSQL não mapeado: {type_name} "
                        f"em {table}.{name}."
                    )
                inline_primary = bool(
                    re.search(r"\bPRIMARY\s+KEY\b", remainder, re.I)
                )
                if inline_primary:
                    primary_key = [name]
                if re.search(r"\bUNIQUE\b", remainder, re.I):
                    unique_keys.add((name,))
                identity = re.search(
                    r"\bGENERATED\s+(BY\s+DEFAULT|ALWAYS)\s+AS\s+IDENTITY\b",
                    remainder,
                    re.I,
                )
                default = None
                if identity is None:
                    default_match = re.search(
                        r"\bDEFAULT\s+(.+?)(?=\s+CHECK\s*\(|$)",
                        remainder,
                        re.I | re.S,
                    )
                    if default_match:
                        default = _normalize_default(default_match.group(1))

                columns[name] = {
                    "type": POSTGRES_TYPES[type_name.upper()],
                    "nullable": not (
                        bool(re.search(r"\bNOT\s+NULL\b", remainder, re.I))
                        or inline_primary
                    ),
                    "primary_key": inline_primary,
                    "identity": (
                        re.sub(r"\s+", " ", identity.group(1).upper())
                        if identity
                        else None
                    ),
                    "default": default,
                }
                inline_fk = re.search(
                    r"\bREFERENCES\s+(\w+)\s*\(([^)]+)\)(.*)$",
                    remainder,
                    re.I,
                )
                if inline_fk:
                    on_delete = re.search(
                        r"\bON\s+DELETE\s+(RESTRICT|CASCADE|SET NULL|SET DEFAULT|NO ACTION)\b",
                        inline_fk.group(3),
                        re.I,
                    )
                    foreign_keys.append(
                        (
                            (name,),
                            inline_fk.group(1),
                            tuple(
                                column.strip()
                                for column in inline_fk.group(2).split(",")
                            ),
                            on_delete.group(1).upper()
                            if on_delete
                            else "NO ACTION",
                        )
                    )

            if not primary_key:
                primary_key = [
                    name
                    for name, metadata in columns.items()
                    if metadata["primary_key"]
                ]
            for name in primary_key:
                columns[name]["nullable"] = False
            tables[table] = {
                "columns": columns,
                "primary_key": primary_key,
                "unique_keys": unique_keys,
                "foreign_keys": foreign_keys,
                "check_constraints": check_constraints,
                "check_count": len(check_constraints),
            }

        index_pattern = (
            r"CREATE\s+(UNIQUE\s+)?INDEX\s+(\w+)\s+ON\s+(\w+)\s*"
            r"\(([^)]+)\)(?:\s+WHERE\s+([^;]+))?"
        )
        for match in re.finditer(index_pattern, sql, re.I):
            unique, name, table, column_list, predicate = match.groups()
            columns = tuple(
                re.sub(r"\s+(ASC|DESC)\s*$", "", column.strip(), flags=re.I)
                for column in column_list.split(",")
            )
            indexes[name] = {
                "table": table,
                "unique": bool(unique),
                "columns": columns,
                "partial": bool(predicate),
            }
    return tables, indexes


def open_sqlite_readonly(sqlite_path):
    path = Path(sqlite_path).resolve(strict=True)
    if not path.is_file():
        raise PreflightError(f"O caminho SQLite não é um arquivo: {path}")
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode=ro",
        uri=True,
        timeout=5,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _sqlite_schema(connection):
    table_definitions = {
        row["name"]: row["sql"] or ""
        for row in connection.execute(
            """
            SELECT name, sql FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
              AND name <> 'schema_migrations'
            ORDER BY name
            """
        )
    }
    tables = {}
    for table, definition in table_definitions.items():
        quoted = _quote_identifier(table)
        column_rows = connection.execute(
            f"PRAGMA table_info({quoted})"
        ).fetchall()
        columns = {}
        primary_key = []
        for row in column_rows:
            name = row["name"]
            primary_ordinal = row["pk"]
            if primary_ordinal:
                primary_key.append((primary_ordinal, name))
            columns[name] = {
                "type": (row["type"] or "").upper(),
                "nullable": not bool(row["notnull"]),
                "primary_key": primary_ordinal > 0,
                "default": _normalize_default(row["dflt_value"]),
            }
        primary_key = [
            name
            for _, name in sorted(primary_key)
        ]

        foreign_key_rows = connection.execute(
            f"PRAGMA foreign_key_list({quoted})"
        ).fetchall()
        foreign_key_groups = defaultdict(list)
        for row in foreign_key_rows:
            foreign_key_groups[row["id"]].append(row)
        foreign_keys = []
        for rows in foreign_key_groups.values():
            rows.sort(key=lambda row: row["seq"])
            foreign_keys.append(
                (
                    tuple(row["from"] for row in rows),
                    rows[0]["table"],
                    tuple(row["to"] for row in rows),
                    rows[0]["on_delete"].upper(),
                )
            )

        unique_keys = set()
        indexes = []
        for index_row in connection.execute(
            f"PRAGMA index_list({quoted})"
        ).fetchall():
            index_name = index_row["name"]
            index_info = connection.execute(
                f"PRAGMA index_info({_quote_identifier(index_name)})"
            ).fetchall()
            index_columns = tuple(
                row["name"]
                for row in sorted(index_info, key=lambda row: row["seqno"])
            )
            partial = bool(index_row["partial"])
            if index_row["unique"] and index_row["origin"] != "pk":
                unique_keys.add((index_columns, partial))
            if index_row["origin"] == "c":
                indexes.append(
                    {
                        "name": index_name,
                        "unique": bool(index_row["unique"]),
                        "columns": index_columns,
                        "partial": partial,
                    }
                )
        tables[table] = {
            "columns": columns,
            "primary_key": primary_key,
            "unique_keys": unique_keys,
            "foreign_keys": foreign_keys,
            "indexes": sorted(indexes, key=lambda index: index["name"]),
            "check_constraints": _extract_check_expressions(definition),
        }
    return tables


def _validate_schema(sqlite_tables, postgres_tables, postgres_indexes):
    divergences = []
    check_comparison = _compare_check_constraints(
        sqlite_tables, postgres_tables
    )
    source_names = set(sqlite_tables)
    target_names = set(postgres_tables)
    if source_names != target_names:
        raise PreflightError(
            "Tabelas divergentes: "
            f"SQLite sem PostgreSQL={sorted(source_names - target_names)}; "
            f"PostgreSQL sem SQLite={sorted(target_names - source_names)}."
        )

    for table, source in sqlite_tables.items():
        target = postgres_tables[table]
        source_columns = set(source["columns"])
        target_columns = set(target["columns"])
        if source_columns != target_columns:
            raise PreflightError(
                f"Colunas divergentes em {table}: "
                f"SQLite sem PostgreSQL={sorted(source_columns - target_columns)}; "
                f"PostgreSQL sem SQLite={sorted(target_columns - source_columns)}."
            )
        for name, source_column in source["columns"].items():
            target_column = target["columns"][name]
            compatible = SQLITE_TYPE_COMPATIBILITY.get(
                target_column["type"], set()
            )
            if source_column["type"] not in compatible:
                raise PreflightError(
                    f"Tipo divergente em {table}.{name}: "
                    f"SQLite={source_column['type']!r}, "
                    f"PostgreSQL={target_column['type']!r}."
                )
            source_nullable = source_column["nullable"]
            if source_column["primary_key"]:
                source_nullable = False
            if source_nullable != target_column["nullable"]:
                raise PreflightError(
                    f"Nullability divergente em {table}.{name}: "
                    f"SQLite={source_nullable}, "
                    f"PostgreSQL={target_column['nullable']}."
                )
            target_default = target_column["default"]
            if source_column["default"] != target_default:
                raise PreflightError(
                    f"Default divergente em {table}.{name}: "
                    f"SQLite={source_column['default']!r}, "
                    f"PostgreSQL={target_default!r}."
                )
        if source["primary_key"] != target["primary_key"]:
            raise PreflightError(
                f"Chave primária divergente em {table}: "
                f"SQLite={source['primary_key']}, "
                f"PostgreSQL={target['primary_key']}."
            )
        if set(source["foreign_keys"]) != set(target["foreign_keys"]):
            raise PreflightError(
                f"Chaves estrangeiras divergentes em {table}: "
                f"SQLite={source['foreign_keys']}, "
                f"PostgreSQL={target['foreign_keys']}."
            )

        expected_unique_keys = {
            (columns, False)
            for columns in target["unique_keys"]
        }
        expected_unique_keys.update(
            (index["columns"], index["partial"])
            for index in postgres_indexes.values()
            if index["table"] == table and index["unique"]
        )
        if source["unique_keys"] != expected_unique_keys:
            raise PreflightError(
                f"Unicidade divergente em {table}: "
                f"SQLite={sorted(source['unique_keys'])}, "
                f"PostgreSQL={sorted(expected_unique_keys)}."
            )

        source_indexes = {
            index["name"]: (
                index["unique"],
                index["columns"],
                index["partial"],
            )
            for index in source["indexes"]
        }
        target_indexes = {
            name: (
                index["unique"],
                index["columns"],
                index["partial"],
            )
            for name, index in postgres_indexes.items()
            if index["table"] == table
        }
        if source_indexes != target_indexes:
            raise PreflightError(
                f"Índices explícitos divergentes em {table}: "
                f"SQLite={source_indexes}, PostgreSQL={target_indexes}."
            )

    divergences.extend(_identity_divergences(sqlite_tables, postgres_tables))
    divergences.extend(
        {
            "kind": "check_constraints",
            **difference,
        }
        for difference in check_comparison["unresolved_differences"]
    )
    return {
        "divergences": divergences,
        "check_comparison": check_comparison,
    }


def _topological_order(postgres_tables):
    dependencies = {
        table: {
            referenced_table
            for _columns, referenced_table, _ref_columns, _action
            in metadata["foreign_keys"]
        }
        for table, metadata in postgres_tables.items()
    }
    ordered = []
    remaining = set(dependencies)
    while remaining:
        ready = sorted(
            table
            for table in remaining
            if not (dependencies[table] & remaining)
        )
        if not ready:
            raise PreflightError(
                "Dependências cíclicas entre tabelas impedem a ordem de carga."
            )
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ordered, dependencies


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_postgres_destination(connection, require_read_only=True):
    identity = connection.execute(
        """
        SELECT current_database(), current_user,
               inet_server_addr()::text, inet_server_port(),
               current_setting('transaction_read_only')
        """
    ).fetchone()
    try:
        server_ip = str(ipaddress.ip_interface(identity[2]).ip)
    except (TypeError, ValueError) as error:
        raise PreflightError(
            "Não foi possível confirmar o endereço IPv4 local do PostgreSQL.",
            diagnostic_code="TARGET_IDENTITY_MISMATCH",
        ) from error
    if (
        identity[0] != "palavra_dia_teste"
        or identity[1] != "postgres"
        or server_ip != "127.0.0.1"
        or identity[3] != 5432
    ):
        raise PreflightError(
            "Destino recusado: exige palavra_dia_teste, postgres, "
            "127.0.0.1:5432.",
            diagnostic_code="TARGET_IDENTITY_MISMATCH",
            diagnostic_details={
                "database_matches": identity[0] == "palavra_dia_teste",
                "user_matches": identity[1] == "postgres",
                "host_matches": server_ip == "127.0.0.1",
                "port_matches": identity[3] == 5432,
            },
        )
    if require_read_only and identity[4] != "on":
        raise PreflightError(
            "A conexão PostgreSQL não está em modo somente leitura."
        )
    user_schemas = connection.execute(
        """
        SELECT nspname FROM pg_namespace
        WHERE nspname NOT IN ('public', 'information_schema', 'pg_catalog')
          AND nspname !~ '^pg_(toast|temp_[0-9]+|toast_temp_[0-9]+)$'
        ORDER BY nspname
        """
    ).fetchall()
    objects = connection.execute(
        """
        SELECT n.nspname, c.relname, c.relkind
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
        ORDER BY n.nspname, c.relname
        """
    ).fetchall()
    custom_types = connection.execute(
        """
        SELECT n.nspname, t.typname, t.typtype
        FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public'
          AND t.typtype IN ('c', 'd', 'e', 'r')
          AND t.typrelid = 0
        ORDER BY t.typname
        """
    ).fetchall()
    schema_objects = connection.execute(
        """
        SELECT 'collation', c.collname
        FROM pg_collation c
        JOIN pg_namespace n ON n.oid = c.collnamespace
        WHERE n.nspname = 'public'
        UNION ALL
        SELECT 'operator', o.oprname
        FROM pg_operator o
        JOIN pg_namespace n ON n.oid = o.oprnamespace
        WHERE n.nspname = 'public'
        UNION ALL
        SELECT 'conversion', c.conname
        FROM pg_conversion c
        JOIN pg_namespace n ON n.oid = c.connamespace
        WHERE n.nspname = 'public'
        UNION ALL
        SELECT 'operator class', o.opcname
        FROM pg_opclass o
        JOIN pg_namespace n ON n.oid = o.opcnamespace
        WHERE n.nspname = 'public'
        UNION ALL
        SELECT 'operator family', o.opfname
        FROM pg_opfamily o
        JOIN pg_namespace n ON n.oid = o.opfnamespace
        WHERE n.nspname = 'public'
        UNION ALL
        SELECT 'text search configuration', c.cfgname
        FROM pg_ts_config c
        JOIN pg_namespace n ON n.oid = c.cfgnamespace
        WHERE n.nspname = 'public'
        UNION ALL
        SELECT 'text search dictionary', d.dictname
        FROM pg_ts_dict d
        JOIN pg_namespace n ON n.oid = d.dictnamespace
        WHERE n.nspname = 'public'
        UNION ALL
        SELECT 'text search parser', p.prsname
        FROM pg_ts_parser p
        JOIN pg_namespace n ON n.oid = p.prsnamespace
        WHERE n.nspname = 'public'
        UNION ALL
        SELECT 'text search template', t.tmplname
        FROM pg_ts_template t
        JOIN pg_namespace n ON n.oid = t.tmplnamespace
        WHERE n.nspname = 'public'
        UNION ALL
        SELECT 'extension', e.extname
        FROM pg_extension e
        JOIN pg_namespace n ON n.oid = e.extnamespace
        WHERE n.nspname = 'public'
        UNION ALL
        SELECT 'publication', p.pubname
        FROM pg_publication p
        WHERE p.puballtables OR EXISTS (
            SELECT 1
            FROM pg_publication_rel r
            JOIN pg_class c ON c.oid = r.prrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE r.prpubid = p.oid AND n.nspname = 'public'
        )
        ORDER BY 1, 2
        """
    ).fetchall()
    user_functions = connection.execute(
        """
        SELECT n.nspname, p.proname, p.oid::text
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
        ORDER BY p.proname
        """
    ).fetchall()
    large_objects = connection.execute(
        "SELECT EXISTS (SELECT 1 FROM pg_largeobject_metadata LIMIT 1)"
    ).fetchone()[0]
    if (
        user_schemas
        or objects
        or custom_types
        or schema_objects
        or user_functions
        or large_objects
    ):
        raise PreflightError(
            "Destino PostgreSQL não está vazio: "
            f"schemas adicionais={list(user_schemas)}, "
            f"objetos={list(objects)}, tipos={list(custom_types)}, "
            f"objetos de schema={list(schema_objects)}, "
            f"funções={list(user_functions)}, "
            f"large_objects={bool(large_objects)}.",
            diagnostic_code="TARGET_NOT_EMPTY",
            diagnostic_details={
                "additional_schema_count": len(user_schemas),
                "public_object_count": len(objects),
                "custom_type_count": len(custom_types),
                "schema_object_count": len(schema_objects),
                "public_function_count": len(user_functions),
                "large_objects_present": bool(large_objects),
            },
        )
    return {
        "database": identity[0],
        "user": identity[1],
        "server_ip": server_ip,
        "port": identity[3],
        "read_only": identity[4] == "on",
        "empty": True,
        "user_objects": 0,
        "additional_schemas": 0,
    }


def run_preflight(
    sqlite_path=DEFAULT_SQLITE_PATH,
    postgres_connection=None,
    postgres_migrations_path=POSTGRES_MIGRATIONS_PATH,
    sqlite_only=False,
):
    if postgres_connection is None and not sqlite_only:
        raise PreflightError(
            "É necessária uma conexão PostgreSQL local somente leitura."
        )
    source_hash_before = _sha256_file(sqlite_path)
    connection = open_sqlite_readonly(sqlite_path)
    try:
        integrity = [
            row[0] for row in connection.execute("PRAGMA integrity_check")
        ]
        if integrity != ["ok"]:
            raise PreflightError(
                f"PRAGMA integrity_check falhou: {integrity}."
            )
        foreign_key_errors = [
            tuple(row)
            for row in connection.execute("PRAGMA foreign_key_check")
        ]
        if foreign_key_errors:
            raise PreflightError(
                f"PRAGMA foreign_key_check encontrou erros: "
                f"{foreign_key_errors[:10]}."
            )

        sqlite_tables = _sqlite_schema(connection)
        postgres_tables, postgres_indexes = _parse_postgres_migrations(
            postgres_migrations_path
        )
        schema_validation = _validate_schema(
            sqlite_tables, postgres_tables, postgres_indexes
        )
        source_type_validation = _validate_postgres_typed_values(
            connection, postgres_tables
        )
        migration_versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        ]
        if migration_versions != list(range(1, 9)):
            raise PreflightError(
                "O SQLite não possui as migrações 001–008 aplicadas em ordem: "
                f"{migration_versions}."
            )

        table_counts = {}
        table_report = {}
        for table, source in sqlite_tables.items():
            count = connection.execute(
                f"SELECT COUNT(*) FROM {_quote_identifier(table)}"
            ).fetchone()[0]
            table_counts[table] = count
            primary_key = source["primary_key"]
            id_bounds = None
            if primary_key == ["id"]:
                bounds = connection.execute(
                    f"SELECT MIN(id), MAX(id) FROM {_quote_identifier(table)}"
                ).fetchone()
                id_bounds = (
                    {"min": bounds[0], "max": bounds[1]}
                    if count
                    else None
                )
            table_report[table] = {
                "count": count,
                "columns": [
                    {
                        "name": name,
                        "sqlite_type": column["type"],
                        "postgres_type": postgres_tables[table]["columns"][name][
                            "type"
                        ],
                        "nullable": postgres_tables[table]["columns"][name][
                            "nullable"
                        ],
                    }
                    for name, column in source["columns"].items()
                ],
                "primary_key": primary_key,
                "id_range": id_bounds,
                "dependencies": sorted(
                    {
                        foreign_key[1]
                        for foreign_key in postgres_tables[table]["foreign_keys"]
                    }
                ),
                "foreign_keys": [
                    {
                        "columns": list(columns),
                        "references": referenced_table,
                        "referenced_columns": list(referenced_columns),
                        "on_delete": on_delete,
                    }
                    for (
                        columns,
                        referenced_table,
                        referenced_columns,
                        on_delete,
                    ) in postgres_tables[table]["foreign_keys"]
                ],
                "indexes": source["indexes"],
                "unique_keys": [
                    {
                        "columns": list(columns),
                        "partial": partial,
                    }
                    for columns, partial in sorted(source["unique_keys"])
                ],
                "postgres_check_constraints": postgres_tables[table][
                    "check_count"
                ],
            }
        table_order, dependencies = _topological_order(postgres_tables)
        destination = (
            None
            if sqlite_only
            else inspect_postgres_destination(postgres_connection)
        )
    finally:
        connection.close()

    source_hash_after = _sha256_file(sqlite_path)
    if source_hash_before != source_hash_after:
        raise PreflightError(
            "O hash do SQLite mudou durante o preflight; interrompido."
        )

    return {
        "mode": "sqlite-only" if sqlite_only else "dry-run",
        "transfer_executed": False,
        "ready_for_transfer": (
            not schema_validation["divergences"]
            and not sqlite_only
        ),
        "source_type_validation": source_type_validation,
        "schema_comparison": {
            "matches": not schema_validation["divergences"],
            "divergences": schema_validation["divergences"],
            "check_comparison": schema_validation["check_comparison"],
        },
        "source": {
            "path": str(Path(sqlite_path).resolve()),
            "mode": "read-only",
            "sha256": source_hash_before,
            "sha256_unchanged": True,
            "integrity_check": "ok",
            "foreign_key_errors": 0,
            "schema_migrations": migration_versions,
            "tables": table_report,
            "total_rows": sum(table_counts.values()),
        },
        "destination": destination,
        "load_order": table_order,
        "dependencies": {
            table: sorted(referenced)
            for table, referenced in sorted(dependencies.items())
        },
        "migration_safety": {
            "backup_required_before_transfer": True,
            "backup_created": False,
            "single_postgres_transaction_required": True,
            "rollback_on_any_error_required": True,
            "copy_primary_key_values_explicitly": True,
            "restart_identity_after_copy_required": True,
            "post_migration_validation_required": [
                "all table counts",
                "foreign-key integrity",
                "content checksums",
                "owner_key preservation",
            ],
            "transfer_implementation_present": True,
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Preflight e migração segura do SQLite para PostgreSQL local."
        )
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument(
        "--dry-run",
        action="store_true",
        help="Executa preflight local e verifica o destino PostgreSQL somente leitura.",
    )
    modes.add_argument(
        "--sqlite-only",
        action="store_true",
        help="Verifica apenas o SQLite de origem e as migrações locais, sem PostgreSQL.",
    )
    modes.add_argument(
        "--migrate-local-test",
        action="store_true",
        help=(
            "Migra para palavra_dia_teste em 127.0.0.1:5432, "
            "após confirmar identidade e destino vazio."
        ),
    )
    modes.add_argument(
        "--diagnose-local-schema",
        action="store_true",
        help=(
            "Reproduz a validação do esquema em transação local temporária "
            "e sempre executa ROLLBACK."
        ),
    )
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=DEFAULT_SQLITE_PATH,
        help="Caminho do SQLite de origem (padrão: banco do projeto).",
    )
    parser.add_argument(
        "--backup-directory",
        type=Path,
        help=(
            "Diretório externo ao projeto para novo backup verificável "
            "(padrão: ~/Desktop/PalavraDoDia-backups)."
        ),
    )
    args = parser.parse_args(argv)
    if args.sqlite_only:
        try:
            report = run_preflight(args.sqlite, sqlite_only=True)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        except (OSError, sqlite3.Error, PreflightError) as error:
            code = (
                error.diagnostic_code
                if isinstance(error, PreflightError)
                else "SQLITE_PREFLIGHT_FAILED"
            )
            print(
                "Preflight interrompido; "
                f"{_format_preflight_diagnostic(error)}."
                if isinstance(error, PreflightError)
                else f"Preflight interrompido; code={code}.",
                file=sys.stderr,
            )
            return 2

    try:
        import getpass

        import psycopg

        if not sys.stdin.isatty() or (
            os.name == "nt" and sys.stdin is not sys.__stdin__
        ):
            raise PreflightError(
                "A senha só pode ser solicitada em um terminal interativo "
                "com entrada oculta.",
                diagnostic_code="INTERACTIVE_TERMINAL_REQUIRED",
            )
        password = getpass.getpass(
            "Senha do PostgreSQL local (entrada oculta): "
        )
        if not password:
            raise PreflightError(
                "Senha vazia; operação cancelada.",
                diagnostic_code="PASSWORD_REQUIRED",
            )
        connection_options = {
            "host": "127.0.0.1",
            "port": 5432,
            "dbname": "palavra_dia_teste",
            "user": "postgres",
            "connect_timeout": 5,
        }
        if args.dry_run:
            connection_options["options"] = (
                "-c default_transaction_read_only=on"
            )
        try:
            postgres_connection = psycopg.connect(
                **connection_options,
                password=password,
            )
        except Exception as error:
            raise PreflightError(
                "A conexão com o PostgreSQL local falhou.",
                diagnostic_code="POSTGRES_CONNECTION_FAILED",
            ) from error
        del password

        with postgres_connection:
            if args.dry_run:
                try:
                    report = run_preflight(args.sqlite, postgres_connection)
                except Exception as error:
                    raise PreflightError(
                        "A validação somente leitura foi interrompida.",
                        diagnostic_code="POSTGRES_PREFLIGHT_FAILED",
                    ) from error
            elif args.diagnose_local_schema:
                from scripts.sqlite_postgres_transfer import (
                    diagnose_postgres_schema,
                )

                report = diagnose_postgres_schema(postgres_connection)
                if not report["rollback_confirmed"]:
                    raise PreflightError(
                        "A reversão do diagnóstico não pôde ser confirmada.",
                        diagnostic_code="DIAGNOSTIC_ROLLBACK_NOT_CONFIRMED",
                    )
            else:
                from scripts.sqlite_postgres_transfer import migrate_to_postgres

                report = migrate_to_postgres(
                    postgres_connection,
                    sqlite_path=args.sqlite,
                    backup_root=args.backup_directory
                    if args.backup_directory
                    else None,
                )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.diagnose_local_schema:
            if not report["rollback_confirmed"] or report["status"] == "aborted":
                return 2
            if report["status"] == "schema_mismatch":
                return 1
        return 0
    except (OSError, sqlite3.Error, PreflightError) as error:
        if isinstance(error, PreflightError):
            code = error.diagnostic_code
        elif isinstance(error, sqlite3.Error):
            code = "SQLITE_OPERATION_FAILED"
        else:
            code = "LOCAL_IO_FAILED"
        diagnostic = (
            _format_preflight_diagnostic(error)
            if isinstance(error, PreflightError)
            else f"code={code}"
        )
        print(f"Operação interrompida; {diagnostic}.", file=sys.stderr)
        return 2
    except Exception:
        print(
            "Operação interrompida; code=UNEXPECTED_OPERATION_FAILURE.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
