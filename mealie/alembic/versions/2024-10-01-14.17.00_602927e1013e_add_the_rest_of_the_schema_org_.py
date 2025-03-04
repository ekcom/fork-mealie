"""'add the rest of the schema.org nutrition properties'

Revision ID: 602927e1013e
Revises: 1fe4bd37ccc8
Create Date: 2024-10-01 14:17:00.611398

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "602927e1013e"
down_revision: str | None = "1fe4bd37ccc8"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("recipe_nutrition", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cholesterol_content", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("saturated_fat_content", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("trans_fat_content", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("unsaturated_fat_content", sa.String(), nullable=True))

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table("recipe_nutrition", schema=None) as batch_op:
        batch_op.drop_column("unsaturated_fat_content")
        batch_op.drop_column("trans_fat_content")
        batch_op.drop_column("saturated_fat_content")
        batch_op.drop_column("cholesterol_content")

    # ### end Alembic commands ###
