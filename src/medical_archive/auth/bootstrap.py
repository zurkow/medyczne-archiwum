import argparse
import getpass
import hmac

from sqlalchemy.orm import Session

from medical_archive.auth.service import (
    BootstrapAlreadyComplete,
    create_first_admin,
)
from medical_archive.config import Settings
from medical_archive.db import create_database


def main(arguments: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Create the first administrator account for Medyczne Archiwum."
    )
    parser.add_argument("--email", required=True, help="Email address for the first administrator.")
    options = parser.parse_args(arguments)

    password = getpass.getpass("Administrator password: ")
    confirmation = getpass.getpass("Confirm administrator password: ")
    if not hmac.compare_digest(password, confirmation):
        parser.error("The passwords do not match.")

    settings = Settings()
    if settings.database_url is None:
        parser.error("Configure MEDICAL_ARCHIVE_DATABASE_URL before bootstrapping.")
    engine = create_database(settings.database_url)
    try:
        with Session(engine) as database:
            try:
                user = create_first_admin(
                    database,
                    email=options.email,
                    password=password,
                )
                created_email = user.email
            except BootstrapAlreadyComplete as error:
                raise SystemExit("Administrator bootstrap has already been completed.") from error
            except ValueError as error:
                parser.error(str(error))
        print(f"Administrator created for {created_email}.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
