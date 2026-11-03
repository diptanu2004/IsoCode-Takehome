from sqlalchemy import Connection, insert

from app.schema import audit_log


def record(
    conn: Connection,
    actor_id: int | None,
    action: str,
    entity: str,
    entity_id: int | str,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    """Write who changed what, in the same transaction as the change itself."""
    conn.execute(
        insert(audit_log).values(
            actor_id=actor_id,
            action=action,
            entity=entity,
            entity_id=str(entity_id),
            before=before,
            after=after,
        )
    )
