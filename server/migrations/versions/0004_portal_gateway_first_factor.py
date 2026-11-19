"""портал сотрудника: вид сессии, отметка первой регистрации фактора; источник первого фактора у RADIUS-клиента

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("admin_sessions", sa.Column("kind", sa.String(length=16), nullable=False, server_default="admin"))
    op.add_column("users", sa.Column("mfa_enrolled_at", sa.DateTime(timezone=True)))
    # у кого фактор уже был, тот не может заново зарегистрироваться по одному паролю
    op.execute("""UPDATE users SET mfa_enrolled_at = now() WHERE
                  EXISTS (SELECT 1 FROM totp_factors t WHERE t.user_id = users.id AND t.status = 'active')
                  OR EXISTS (SELECT 1 FROM devices d WHERE d.user_id = users.id)""")
    op.add_column("radius_clients", sa.Column("first_factor", sa.String(length=16), nullable=False, server_default="mfa_gate"))


def downgrade() -> None:
    op.drop_column("radius_clients", "first_factor")
    op.drop_column("users", "mfa_enrolled_at")
    op.drop_column("admin_sessions", "kind")
