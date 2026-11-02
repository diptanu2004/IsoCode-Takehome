"""Load the demo setup: users, partners, report sources, rate cards and saved column mappings.

    uv run python -m scripts.seed            # add whatever is missing
    uv run python -m scripts.seed --reset    # empty every table first

Safe to run more than once. Demo users come from the SEED_* environment variables.
"""

import argparse
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Connection, insert, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.auth.passwords import hash_password
from app.config import REPO_ROOT
from app.db import get_engine
from app.enums import MappingSource, Role
from app.mapping.fingerprint import header_fingerprint
from app.schema import (
    app_meta,
    mapping_versions,
    metadata,
    partners,
    rate_cards,
    sources,
    users,
)
from scripts.catalog import (
    PARTNERS,
    RATE_CARDS,
    SEED_FORMATS,
    personal_columns,
    saved_mapping,
)


class SeedUsers(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SEED_", env_file=(REPO_ROOT / ".env", ".env"), extra="ignore"
    )

    exec_email: str = "exec@demo.local"
    exec_password: str = ""
    cofounder_email: str = "cofounder@demo.local"
    cofounder_password: str = ""


@dataclass(frozen=True)
class DemoUser:
    email: str
    name: str
    password: str
    role: Role


def demo_users(settings: SeedUsers) -> list[DemoUser]:
    if len(settings.exec_password) < 10 or len(settings.cofounder_password) < 10:
        raise SystemExit(
            "Set SEED_EXEC_PASSWORD and SEED_COFOUNDER_PASSWORD (10+ characters) in .env first."
        )
    return [
        DemoUser(settings.exec_email.lower(), "Kavya Rao", settings.exec_password, Role.EXEC),
        DemoUser(
            settings.cofounder_email.lower(),
            "Arjun Menon",
            settings.cofounder_password,
            Role.COFOUNDER,
        ),
    ]


def seed(conn: Connection, as_of: date, people: list[DemoUser]) -> dict[str, int]:
    """Insert anything missing. Existing rows (and edits made in the app) are left alone."""
    counts = dict.fromkeys(("users", "partners", "sources", "rate_cards", "mappings"), 0)

    for person in people:
        result = conn.execute(
            pg_insert(users)
            .values(
                email=person.email,
                name=person.name,
                password_hash=hash_password(person.password),
                role=person.role.value,
            )
            .on_conflict_do_nothing(index_elements=["email"])
            .returning(users.c.id)
        )
        counts["users"] += len(result.all())
    approver = conn.execute(
        select(users.c.id).where(users.c.role == Role.COFOUNDER.value).order_by(users.c.id)
    ).scalar()

    for p in PARTNERS:
        result = conn.execute(
            pg_insert(partners)
            .values(
                name=p.name,
                kind=p.kind.value,
                settlement_cycle_days=p.settlement_cycle_days,
                tolerance_paise=p.tolerance_paise,
                return_window_days=p.return_window_days,
            )
            .on_conflict_do_nothing(index_elements=["name"])
            .returning(partners.c.id)
        )
        counts["partners"] += len(result.all())
    partner_ids = dict(conn.execute(select(partners.c.name, partners.c.id)).all())

    for name, rules in RATE_CARDS.items():
        has_card = conn.execute(
            select(rate_cards.c.id).where(rate_cards.c.partner_id == partner_ids[name])
        ).first()
        if not has_card:
            conn.execute(
                insert(rate_cards).values(
                    partner_id=partner_ids[name],
                    version=1,
                    effective_from=as_of - timedelta(days=365),
                    rules=rules,
                    note="Contracted rates (seed)",
                    created_by=approver,
                )
            )
            counts["rate_cards"] += 1

    for fmt in SEED_FORMATS:
        result = conn.execute(
            pg_insert(sources)
            .values(
                partner_id=partner_ids[fmt.partner],
                file_kind=fmt.file_kind.value,
                name=fmt.source_name,
            )
            .on_conflict_do_nothing(index_elements=["name"])
            .returning(sources.c.id)
        )
        counts["sources"] += len(result.all())
        source_id = conn.execute(
            select(sources.c.id).where(sources.c.name == fmt.source_name)
        ).scalar_one()
        has_mapping = conn.execute(
            select(mapping_versions.c.id).where(mapping_versions.c.source_id == source_id)
        ).first()
        if not has_mapping:
            conn.execute(
                insert(mapping_versions).values(
                    source_id=source_id,
                    version=1,
                    header_fingerprint=header_fingerprint([c.header for c in fmt.columns]),
                    mapping=saved_mapping(fmt),
                    personal_data_columns=personal_columns(fmt),
                    suggested_by=MappingSource.SEED.value,
                    approved_by=approver,
                    approved_at=datetime.now(UTC),
                    is_active=True,
                )
            )
            counts["mappings"] += 1

    conn.execute(
        pg_insert(app_meta)
        .values(key="seed", value={"as_of": as_of.isoformat()})
        .on_conflict_do_update(index_elements=["key"], set_={"value": {"as_of": as_of.isoformat()}})
    )
    return counts


def reset(conn: Connection) -> None:
    names = ", ".join(t.name for t in metadata.sorted_tables)
    conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="empty every table first")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()

    people = demo_users(SeedUsers())
    with get_engine().begin() as conn:
        if args.reset:
            reset(conn)
        counts = seed(conn, args.as_of, people)
        # Keep demo names and passwords in step with .env when the users already exist.
        for person in people:
            conn.execute(
                update(users)
                .where(users.c.email == person.email)
                .values(name=person.name, password_hash=hash_password(person.password))
            )
    added = ", ".join(f"{n} {k}" for k, n in counts.items())
    print(f"Seed complete (as of {args.as_of}): added {added}.")
    for person in people:
        print(f"  {person.role.value:9} {person.email}")


if __name__ == "__main__":
    main()
