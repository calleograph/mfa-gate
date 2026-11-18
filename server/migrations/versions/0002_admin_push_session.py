"""push-вход в панель привязан к сессии

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auth_requests", sa.Column("session_ref", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("auth_requests", "session_ref")
