"""Allow operator-registered VM targets without replacing Kubernetes targets."""
from alembic import op
import sqlalchemy as sa

revision = '20261020_0001'
down_revision = '20260901_0010'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('resource_targets') as batch:
        batch.drop_constraint('runtime_type',type_='check')
        batch.create_check_constraint('runtime_type',"runtime_type IN ('KUBERNETES', 'VM')")

def downgrade():
    connection=op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM resource_targets WHERE runtime_type='VM'")):
        raise RuntimeError('Disable and remove VM targets before downgrading')
    with op.batch_alter_table('resource_targets') as batch:
        batch.drop_constraint('runtime_type',type_='check')
        batch.create_check_constraint('runtime_type',"runtime_type IN ('KUBERNETES')")
