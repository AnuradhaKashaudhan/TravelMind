"""Enable the PostgreSQL vector extension."""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_enable_vector"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    """Enable pgvector for later database models."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    """Leave the shared vector extension installed on downgrade."""
    pass
