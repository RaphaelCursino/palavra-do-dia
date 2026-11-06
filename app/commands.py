from datetime import date
from pathlib import Path

import click
from flask import current_app

from .db import get_db
from .services.scripture_import import (
    LicenseApproval,
    ScriptureImportError,
    import_translation_file,
)
from .services.blivre_import import (
    BLIVRE_SOURCE_PAGE,
    import_blivre_archive,
)
from .services.editorial_seed import (
    EditorialSeedError,
    load_initial_editorial,
    publish_initial_editorial,
)


def register_commands(app):
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
