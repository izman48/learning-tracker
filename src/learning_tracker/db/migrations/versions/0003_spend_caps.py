"""spend caps: running totals per period and a ledger of paid calls

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

PERIOD_KIND = sa.Enum("month", "day", name="spend_period_kind")
STATE = sa.Enum("reserved", "settled", "settled_full", "overage", "anomaly", name="spend_state")


def upgrade() -> None:
    op.create_table(
        "spend_periods",
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("period_kind", PERIOD_KIND, nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("cap_micros", sa.BigInteger(), nullable=False),
        sa.Column("used_micros", sa.BigInteger(), server_default="0", nullable=False),
        sa.CheckConstraint("cap_micros >= 0", name=op.f("ck_spend_periods_cap_not_negative")),
        sa.CheckConstraint("used_micros >= 0", name=op.f("ck_spend_periods_used_not_negative")),
        sa.PrimaryKeyConstraint(
            "scope_key", "period_kind", "period_start", name=op.f("pk_spend_periods")
        ),
    )
    op.create_table(
        "spend_ledger",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_user_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("step", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("reserved_micros", sa.BigInteger(), nullable=False),
        sa.Column("actual_micros", sa.BigInteger(), nullable=True),
        sa.Column("state", STATE, nullable=False),
        sa.Column("month_start", sa.Date(), nullable=True),
        sa.Column("day_start", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "reserved_micros >= 0", name=op.f("ck_spend_ledger_reserved_not_negative")
        ),
        sa.CheckConstraint(
            "actual_micros IS NULL OR actual_micros >= 0",
            name=op.f("ck_spend_ledger_actual_not_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["scope_user_id"],
            ["users.id"],
            name=op.f("fk_spend_ledger_scope_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spend_ledger")),
    )
    op.create_index(
        op.f("ix_spend_ledger_scope_user_id"), "spend_ledger", ["scope_user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_spend_ledger_scope_user_id"), table_name="spend_ledger")
    op.drop_table("spend_ledger")
    op.drop_table("spend_periods")
    # drop_table leaves enum types behind; remove them so downgrade is complete.
    STATE.drop(op.get_bind(), checkfirst=True)
    PERIOD_KIND.drop(op.get_bind(), checkfirst=True)
