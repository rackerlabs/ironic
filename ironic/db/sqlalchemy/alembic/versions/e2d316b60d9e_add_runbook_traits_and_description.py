#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Add runbook_traits table and description column to runbooks

Revision ID: e2d316b60d9e
Revises: 2a3b4c5d6e7f
Create Date: 2026-03-26 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e2d316b60d9e'
down_revision = '2a3b4c5d6e7f'


def upgrade():
    # Add description column to runbooks
    op.add_column('runbooks',
                  sa.Column('description', sa.String(length=255),
                            nullable=True))

    # Create the runbook_traits table
    op.create_table(
        'runbook_traits',
        sa.Column('version', sa.String(length=15), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('runbook_id', sa.Integer(), nullable=False,
                  autoincrement=False),
        sa.Column('trait', sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint('runbook_id', 'trait'),
        sa.ForeignKeyConstraint(['runbook_id'], ['runbooks.id']),
        sa.Index('runbook_traits_idx', 'trait'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4'
    )

    # NOTE: Data migration from runbook names to traits is handled by
    # the online data migration 'migrate_runbook_names_to_traits'
