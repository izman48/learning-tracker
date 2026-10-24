"""Deleting an account must remove or detach every row that points at it.

Every foreign key to users.id either cascades (the row is the user's data) or is
on SET_NULL_ALLOWED (an audit-style record that outlives the user, detached).
Any new table with a user reference fails here until someone decides which.
"""

from sqlalchemy import Column, ForeignKey, MetaData, Table, Uuid

from learning_tracker.db.models import Base

SET_NULL_ALLOWED = {
    ("password_resets", "created_by"),
}


def user_reference_problems(metadata: MetaData) -> list[str]:
    problems = []
    for table in metadata.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table.name != "users":
                continue
            column = fk.parent.name
            rule = (fk.ondelete or "NO ACTION").upper()
            if (table.name, column) in SET_NULL_ALLOWED:
                if rule != "SET NULL" or not fk.parent.nullable:
                    problems.append(f"{table.name}.{column} must be nullable ON DELETE SET NULL")
            elif rule != "CASCADE":
                problems.append(f"{table.name}.{column} must be ON DELETE CASCADE")
    return problems


def test_every_user_reference_cascades_or_is_an_allowed_set_null() -> None:
    references = [
        fk
        for t in Base.metadata.sorted_tables
        for fk in t.foreign_keys
        if fk.column.table.name == "users"
    ]
    assert len(references) >= 4  # precondition: we are checking the real schema

    assert user_reference_problems(Base.metadata) == []


def test_detector_fires_on_a_reference_with_no_delete_rule() -> None:
    metadata = MetaData()
    Table("users", metadata, Column("id", Uuid, primary_key=True))
    Table("topics", metadata, Column("user_id", Uuid, ForeignKey("users.id")))

    assert user_reference_problems(metadata) == ["topics.user_id must be ON DELETE CASCADE"]
