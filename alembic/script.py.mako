"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | default('None')}
Create Date: ${create_date}

"""
from alembic import op
import sqlalchemy as sa


# ----------------------------------------------------------
# Aquí NO se escriben modelos, sólo instrucciones de cambio.
# Ejemplo:
#
# def upgrade():
#     op.add_column('teachers', sa.Column('phone', sa.String()))
#
# def downgrade():
#     op.drop_column('teachers', 'phone')
# ----------------------------------------------------------


def upgrade():
    ${upgrades if upgrades else "pass"}


def downgrade():
    ${downgrades if downgrades else "pass"}
