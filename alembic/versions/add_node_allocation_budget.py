"""Keep the observed node budget separate from remaining scheduling capacity."""
from alembic import op
import sqlalchemy as sa

revision = "20261022_0001"
down_revision = "20261020_0001"
branch_labels = None
depends_on = None


def upgrade():
    # Existing effective availability cannot reconstruct the total node budget.
    # Keep it unknown until the next authenticated agent observation.
    for name, constraint in (
        ("node_allocatable_cpu_millicores", "node_allocatable_cpu_non_negative"),
        ("node_allocatable_memory_mib", "node_allocatable_memory_non_negative"),
    ):
        op.add_column("resource_targets", sa.Column(name, sa.Integer(), nullable=True))
        op.create_check_constraint(constraint, "resource_targets", f"{name} IS NULL OR {name} >= 0")


def downgrade():
    for name, constraint in (
        ("node_allocatable_memory_mib", "node_allocatable_memory_non_negative"),
        ("node_allocatable_cpu_millicores", "node_allocatable_cpu_non_negative"),
    ):
        op.drop_constraint(constraint, "resource_targets", type_="check")
        op.drop_column("resource_targets", name)
