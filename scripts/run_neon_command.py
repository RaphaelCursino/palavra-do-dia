import getpass
import os
from pathlib import Path
import secrets
import subprocess
import sys


ALLOWED_COMMANDS = {
    "db-upgrade",
    "seed-canonical-books",
    "import-blivre",
    "publish-initial-editorial",
    "verify-content",
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ALLOWED_COMMANDS:
        commands = ", ".join(sorted(ALLOWED_COMMANDS))
        print(f"Uso: python scripts/run_neon_command.py <comando> [opções]")
        print(f"Comandos aceitos: {commands}")
        return 2
    if not sys.stdin.isatty():
        print("Este comando exige terminal interativo para entrada oculta.")
        return 2

    command = sys.argv[1]
    environment = os.environ.copy()
    environment["PALAVRA_DO_DIA_ENV"] = "production"
    environment["PALAVRA_DO_DIA_SECRET_KEY"] = secrets.token_urlsafe(48)
    environment["DATABASE_URL"] = getpass.getpass(
        "URL pooler Neon do usuário da aplicação (entrada oculta): "
    )
    environment.pop("MIGRATION_DATABASE_URL", None)
    if command == "db-upgrade":
        environment["MIGRATION_DATABASE_URL"] = getpass.getpass(
            "URL direta Neon da role de migração (entrada oculta): "
        )

    project_root = Path(__file__).resolve().parent.parent
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "flask",
            "--app",
            "app:create_app",
            command,
            *sys.argv[2:],
        ],
        cwd=project_root,
        env=environment,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
