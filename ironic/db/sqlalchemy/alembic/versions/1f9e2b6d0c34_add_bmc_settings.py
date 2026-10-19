# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""add bmc settings

Revision ID: 1f9e2b6d0c34
Revises: e2d316b60d9e
Create Date: 2026-09-09 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '1f9e2b6d0c34'
down_revision = 'e2d316b60d9e'


def upgrade():
    op.create_table(
        'bmc_settings',
        sa.Column('node_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('attribute_type', sa.String(length=255), nullable=True),
        sa.Column('allowable_values', sa.Text(), nullable=True),
        sa.Column('lower_bound', sa.BigInteger(), nullable=True),
        sa.Column('max_length', sa.Integer(), nullable=True),
        sa.Column('min_length', sa.Integer(), nullable=True),
        sa.Column('read_only', sa.Boolean(), nullable=True),
        sa.Column('reset_required', sa.Boolean(), nullable=True),
        sa.Column('unique', sa.Boolean(), nullable=True),
        sa.Column('upper_bound', sa.BigInteger(), nullable=True),
        sa.Column('version', sa.String(length=15), nullable=True),
        sa.ForeignKeyConstraint(['node_id'], ['nodes.id'], ),
        sa.PrimaryKeyConstraint('node_id', 'name'),
        mysql_engine='InnoDB',
        mysql_charset='UTF8MB3'
    )
