import getpass
import os
import subprocess
import sys
from urllib.parse import quote


DATABASE_NAME = "palavra_dia_pytest"
DATABASE_HOST = "127.0.0.1"
DATABASE_PORT = 5432
DATABASE_URL_ENV = "PALAVRA_DO_DIA_TEST_DATABASE_URL"
RUN_TESTS_ENV = "PALAVRA_DO_DIA_RUN_POSTGRES_TESTS"
ACKNOWLEDGEMENT_ENV = (
    "PALAVRA_DO_DIA_TEST_ACKNOWLEDGE_LOOPBACK_TUNNEL_LIMITATION"
)
ACKNOWLEDGEMENT_VALUE = "I_ACKNOWLEDGE_LOOPBACK_IS_NOT_LOCALITY_PROOF"


def main():
    print(
        "A conexão com 127.0.0.1 não comprova que o PostgreSQL está "
        "fisicamente local; um túnel SSH pode encaminhá-la."
    )
    acknowledgement = input(
        f"Digite {ACKNOWLEDGEMENT_VALUE} para continuar: "
    )
    if acknowledgement != ACKNOWLEDGEMENT_VALUE:
        print("Confirmação ausente; nenhum teste foi iniciado.")
        return 2

    username = input("Usuário PostgreSQL exclusivo de testes: ").strip()
    if not username:
        print("Usuário vazio; nenhum teste foi iniciado.")
        return 2
    password = getpass.getpass("Senha PostgreSQL (entrada oculta): ")

    database_url = (
        f"postgresql://{quote(username, safe='')}:{quote(password, safe='')}"
        f"@{DATABASE_HOST}:{DATABASE_PORT}/{DATABASE_NAME}"
    )
    environment = os.environ.copy()
    environment.pop("DATABASE_URL", None)
    environment[DATABASE_URL_ENV] = database_url
    environment[RUN_TESTS_ENV] = "1"
    environment[ACKNOWLEDGEMENT_ENV] = ACKNOWLEDGEMENT_VALUE
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration",
                "-q",
            ],
            env=environment,
            check=False,
        )
    finally:
        environment.pop(DATABASE_URL_ENV, None)
        del database_url
        del password

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
