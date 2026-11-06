from datetime import date
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

import click
from flask import current_app

from .db import (
    POSTGRES_MIGRATIONS_PATH,
    apply_postgres_migrations,
    get_db,
    validate_neon_database_url,
)
from .services.scripture_import import (
    LicenseApproval,
    ScriptureImportError,
    import_translation_file,
)
from .services.blivre_import import (
    BLIVRE_ATTRIBUTION,
    BLIVRE_LICENSE_URL,
    BLIVRE_SOURCE_PAGE,
    import_blivre_archive,
)
from .services.canonical_books import (
    CanonicalBooksSeedError,
    seed_canonical_books,
)
from .services.editorial_seed import (
    EditorialSeedError,
    load_initial_editorial,
    publish_initial_editorial,
)


_EXPECTED_POSTGRES_TABLES = {
    "achievement_unlocks",
    "books",
    "chapters",
    "daily_readings",
    "favorites",
    "import_batches",
    "prayer_entries",
    "reader_preferences",
    "reading_completions",
    "reading_goals",
    "reflections",
    "schema_migrations",
    "translations",
    "verse_annotations",
    "verse_highlights",
    "verse_notes",
    "verses",
    "weekly_goal_achievements",
}


def _database_url_username(database_url):
    username = urlsplit(database_url).username
    return unquote(username) if username else None


def _validate_migration_role(identity, migration_role):
    if identity is None:
        raise click.ClickException(
            "Não foi possível confirmar a identidade do banco."
        )
    (
        database_name,
        role_name,
        is_superuser,
        can_create_database,
        can_create_roles,
        can_create_schema_objects,
    ) = identity
    if role_name != migration_role:
        raise click.ClickException(
            "A role confirmada pelo servidor não corresponde à URL "
            "de migração."
        )
    if is_superuser or can_create_database or can_create_roles:
        raise click.ClickException(
            "Use uma role de migração sem privilégios de "
            "superusuário, criação de banco ou roles."
        )
    if not can_create_schema_objects:
        raise click.ClickException(
            "A role de migração precisa de CREATE no schema public."
        )
    return database_name, role_name


def _public_table_names(connection):
    return {
        row[0]
        for row in connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        ).fetchall()
    }


def register_commands(app):
    @app.cli.command("db-upgrade")
    @click.option(
        "--confirm",
        is_flag=True,
        help="Confirma a aplicação das migrações no banco Neon configurado.",
    )
    def database_upgrade(confirm):
        if current_app.config["ENVIRONMENT"] != "production":
            raise click.ClickException(
                "db-upgrade só pode ser executado com "
                "PALAVRA_DO_DIA_ENV=production."
            )
        if not confirm:
            raise click.ClickException(
                "Migração bloqueada. Revise o destino e repita com --confirm."
            )

        runtime_url = current_app.config.get("DATABASE_URL")
        migration_url = current_app.config.get("MIGRATION_DATABASE_URL")
        if not runtime_url or not migration_url:
            raise click.ClickException(
                "Configure DATABASE_URL (pooler) e MIGRATION_DATABASE_URL "
                "(endpoint direto) no ambiente do comando."
            )
        try:
            runtime_endpoint, runtime_database = validate_neon_database_url(
                runtime_url,
                require_pooler=True,
            )
            migration_endpoint, migration_database = validate_neon_database_url(
                migration_url,
                require_pooler=False,
            )
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        if (
            runtime_endpoint != migration_endpoint
            or runtime_database != migration_database
        ):
            raise click.ClickException(
                "As URLs de runtime e migração não apontam para o mesmo "
                "endpoint e banco Neon."
            )
        runtime_role = _database_url_username(runtime_url)
        migration_role = _database_url_username(migration_url)
        if not runtime_role or not migration_role or runtime_role == migration_role:
            raise click.ClickException(
                "Configure roles distintas para runtime e migração."
            )

        try:
            import psycopg
        except ImportError as error:
            raise click.ClickException(
                "A dependência psycopg é necessária para as migrações."
            ) from error

        connection = psycopg.connect(migration_url, connect_timeout=10)
        lock_acquired = False
        try:
            identity = connection.execute(
                """
                SELECT current_database(), current_user, role.rolsuper,
                       role.rolcreatedb, role.rolcreaterole,
                       has_schema_privilege(current_user, 'public', 'CREATE')
                FROM pg_roles AS role
                WHERE role.rolname = current_user
                """
            ).fetchone()
            database_name, role_name = _validate_migration_role(
                identity,
                migration_role,
            )
            if database_name in {"palavra_dia_teste", "palavra_dia_pytest"}:
                raise click.ClickException(
                    "Migrações de produção recusadas para banco reservado a testes."
                )

            click.echo(
                f"Destino confirmado pelo servidor: banco={database_name}, "
                f"role={role_name}, endpoint Neon direto."
            )
            connection.execute(
                "SELECT pg_advisory_lock(hashtext(%s))",
                ("palavra-do-dia-schema-migrations",),
            )
            connection.commit()
            lock_acquired = True
            existing_tables = _public_table_names(connection)
            unexpected_tables = existing_tables - _EXPECTED_POSTGRES_TABLES
            if unexpected_tables:
                raise click.ClickException(
                    "Migração interrompida: o schema public contém tabelas "
                    "ou views não reconhecidas."
                )
            if (
                "schema_migrations" not in existing_tables
                and existing_tables
            ):
                raise click.ClickException(
                    "Migração interrompida: o banco não está vazio."
                )
            if "schema_migrations" in existing_tables:
                versions = {
                    row[0]
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations"
                    ).fetchall()
                }
                if not versions.issubset(set(range(1, 9))):
                    raise click.ClickException(
                        "Migração interrompida: o histórico contém versões "
                        "desconhecidas."
                    )

            result = apply_postgres_migrations(
                connection,
                POSTGRES_MIGRATIONS_PATH,
            )
            if _public_table_names(connection) != _EXPECTED_POSTGRES_TABLES:
                raise click.ClickException(
                    "Migração incompleta: o conjunto de tabelas do esquema "
                    "não corresponde às oito migrações."
                )
        except psycopg.Error as error:
            diagnostic_code = getattr(error, "sqlstate", None)
            suffix = (
                f", SQLSTATE={diagnostic_code}"
                if isinstance(diagnostic_code, str)
                and re.fullmatch(r"[0-9A-Z]{5}", diagnostic_code)
                else ""
            )
            raise click.ClickException(
                f"Migração PostgreSQL falhou ({type(error).__name__}{suffix})."
            ) from error
        finally:
            try:
                connection.rollback()
                if lock_acquired:
                    connection.execute(
                        "SELECT pg_advisory_unlock(hashtext(%s))",
                        ("palavra-do-dia-schema-migrations",),
                    )
                    connection.commit()
            finally:
                connection.close()

        click.echo(
            "Migrações verificadas: "
            f'{result["applied_count"]} aplicadas, '
            f'{result["already_applied_count"]} já existentes.'
        )

    @app.cli.command("seed-canonical-books")
    @click.option(
        "--confirm",
        is_flag=True,
        help="Confirma o cadastro do catálogo canônico de 66 livros.",
    )
    def seed_canonical_books_command(confirm):
        if not confirm:
            raise click.ClickException(
                "Carga bloqueada. Revise o destino e repita com --confirm."
            )
        try:
            result = seed_canonical_books(get_db())
        except CanonicalBooksSeedError as error:
            raise click.ClickException(str(error)) from error

        status = "já estavam cadastrados" if result["already_seeded"] else "cadastrados"
        click.echo(
            f"Catálogo canônico {status}: {result['book_count']} livros."
        )

    @app.cli.command("verify-content")
    def verify_content():
        connection = get_db()
        translation = connection.execute(
            """
            SELECT id, license_status, license_name, license_url,
                   attribution_text
            FROM translations
            WHERE code = 'blivre'
            """
        ).fetchone()
        if translation is None:
            raise click.ClickException("BLIVRE não foi importada.")

        counts = {
            "books": connection.execute(
                "SELECT COUNT(*) FROM books"
            ).fetchone()[0],
            "chapters": connection.execute(
                "SELECT COUNT(*) FROM chapters"
            ).fetchone()[0],
            "verses": connection.execute(
                "SELECT COUNT(*) FROM verses WHERE translation_id = ?",
                (translation["id"],),
            ).fetchone()[0],
            "notes": connection.execute(
                """
                SELECT COUNT(*)
                FROM verse_notes
                JOIN verses ON verses.id = verse_notes.verse_id
                WHERE verses.translation_id = ?
                """,
                (translation["id"],),
            ).fetchone()[0],
        }
        expected = {
            "books": 66,
            "chapters": 1189,
            "verses": 31102,
            "notes": 1290,
        }
        if (
            counts != expected
            or translation["license_status"] != "approved"
            or translation["license_name"]
            != "Creative Commons Atribuição 4.0 Brasil (CC BY 4.0)"
            or translation["license_url"] != BLIVRE_LICENSE_URL
            or translation["attribution_text"] != BLIVRE_ATTRIBUTION
        ):
            raise click.ClickException(
                "BLIVRE ou sua atribuição/licença não corresponde às "
                "contagens esperadas; nenhuma alteração foi feita."
            )

        editorial = connection.execute(
            """
            SELECT COUNT(*) AS reading_count,
                   MIN(reading_date) AS first_date,
                   MAX(reading_date) AS last_date
            FROM daily_readings
            WHERE translation_id = ?
            """,
            (translation["id"],),
        ).fetchone()
        reflection_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM reflections
            JOIN daily_readings
              ON daily_readings.id = reflections.daily_reading_id
            WHERE daily_readings.translation_id = ?
              AND reflections.status = 'published'
            """,
            (translation["id"],),
        ).fetchone()[0]

        click.echo(
            "BLIVRE validada: "
            f'{counts["books"]} livros, {counts["chapters"]} capítulos, '
            f'{counts["verses"]} versículos, {counts["notes"]} notas; '
            f'licença={translation["license_name"]}; '
            f'atribuição={translation["license_url"]}.'
        )
        click.echo(
            f'Conteúdo editorial publicado: {editorial["reading_count"]} '
            f'leituras ({editorial["first_date"]} a {editorial["last_date"]}), '
            f'{reflection_count} reflexões.'
        )

    @app.cli.command("import-scripture")
    @click.argument("file_path", type=click.Path(exists=True, dir_okay=False))
    @click.option(
        "--confirm-license",
        is_flag=True,
        help="Confirma que os direitos de armazenar e exibir foram verificados.",
    )
    @click.option("--reviewed-by", help="Responsável pela verificação da licença.")
    @click.option("--reviewed-at", help="Data da verificação (AAAA-MM-DD).")
    @click.option(
        "--evidence-url",
        help="URL HTTPS da evidência de autorização/licença verificada.",
    )
    def import_scripture(
        file_path, confirm_license, reviewed_by, reviewed_at, evidence_url
    ):
        if not all((confirm_license, reviewed_by, reviewed_at, evidence_url)):
            raise click.ClickException(
                "Importação bloqueada. Após verificar os direitos, informe "
                "--confirm-license, --reviewed-by, --reviewed-at e --evidence-url."
            )
        try:
            date.fromisoformat(reviewed_at)
            result = import_translation_file(
                get_db(),
                file_path,
                LicenseApproval(
                    reviewed_by=reviewed_by,
                    reviewed_at=reviewed_at,
                    evidence_url=evidence_url,
                    confirmed=confirm_license,
                ),
            )
        except ValueError as error:
            raise click.ClickException(str(error)) from error

        current_app.logger.info(
            "Importação concluída: tradução %s, %s livros, %s capítulos, %s versículos.",
            result["translation_code"],
            result["book_count"],
            result["chapter_count"],
            result["verse_count"],
        )
        click.echo(
            "Importação concluída: "
            f"{result['translation_code']} — {result['book_count']} livro(s), "
            f"{result['chapter_count']} capítulo(s), {result['verse_count']} versículo(s)."
        )

    @app.cli.command("import-blivre")
    @click.option(
        "--confirm-license",
        is_flag=True,
        help="Confirma que a atribuição e o uso sob CC BY 4.0 foram verificados.",
    )
    @click.option("--reviewed-by", required=True, help="Responsável pela revisão.")
    @click.option("--reviewed-at", required=True, help="Data da revisão (AAAA-MM-DD).")
    @click.option(
        "--acquired-at",
        required=True,
        help="Data de obtenção do arquivo oficial (AAAA-MM-DD).",
    )
    def import_blivre(confirm_license, reviewed_by, reviewed_at, acquired_at):
        if not confirm_license:
            raise click.ClickException(
                "Importação bloqueada. Confirme a licença CC BY 4.0 e a "
                "atribuição oficial com --confirm-license."
            )
        archive_path = (
            Path(current_app.root_path).parent
            / "data"
            / "imports"
            / "blivre"
            / "porbr2018_usfm.zip"
        )
        if not archive_path.is_file():
            raise click.ClickException(
                "Arquivo data/imports/blivre/porbr2018_usfm.zip não encontrado; "
                "a importação foi interrompida."
            )
        try:
            result = import_blivre_archive(
                get_db(),
                archive_path,
                LicenseApproval(
                    reviewed_by=reviewed_by,
                    reviewed_at=reviewed_at,
                    evidence_url=BLIVRE_SOURCE_PAGE,
                    confirmed=True,
                ),
                acquired_at,
            )
        except (ValueError, OSError) as error:
            raise click.ClickException(str(error)) from error

        status = "já estava importada" if result["already_imported"] else "importada"
        click.echo(
            f"BLIVRE {status}: {result['book_count']} livro(s), "
            f"{result['chapter_count']} capítulo(s), "
            f"{result['verse_count']} versículo(s), "
            f"{result['note_count']} nota(s); "
            f"SHA-256 {result.get('source_sha256', 'verificado')}."
        )

    @app.cli.command("publish-initial-editorial")
    @click.option(
        "--confirm-publish",
        is_flag=True,
        help="Confirma a publicação do conteúdo editorial inicial revisado.",
    )
    def publish_initial_editorial_command(confirm_publish):
        if not confirm_publish:
            raise click.ClickException(
                "Publicação bloqueada. Confirme o conteúdo editorial com "
                "--confirm-publish."
            )
        editorial_path = (
            Path(current_app.root_path).parent
            / "data"
            / "editorial"
            / "blivre-initial-week.json"
        )
        try:
            payload = load_initial_editorial(editorial_path)
            result = publish_initial_editorial(get_db(), payload)
            get_db().commit()
        except EditorialSeedError as error:
            get_db().rollback()
            raise click.ClickException(str(error)) from error

        status = "já estava publicada" if result["already_published"] else "publicada"
        click.echo(
            f"Semana editorial {status}: "
            f'{result["reading_count"]} leituras, '
            f'{result["reflection_count"]} reflexões; '
            f'{result["inserted_reading_count"]} leituras e '
            f'{result["inserted_reflection_count"]} reflexões adicionadas.'
        )
        for reference in result["references"]:
            click.echo(f"- {reference}")
