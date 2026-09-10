"""Initial persistence schema

Revision ID: 0001
Revises: none
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('telegram_user_id', sa.BigInteger(), nullable=False),
    sa.Column('username', sa.String(length=255), nullable=True),
    sa.Column('telegram_display_name', sa.Text(), nullable=True),
    sa.Column('display_name', sa.Text(), nullable=True),
    sa.Column('role', sa.Enum('ADMINISTRATOR', 'COURIER', 'CUSTOMER', name='user_role', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('active', sa.Boolean(create_constraint=True, name='active_bool'), server_default=sa.text('1'), nullable=False),
    sa.Column('registered_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users'))
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index('uq_users_active_administrator', ['role'], unique=True, sqlite_where=sa.text("active = 1 AND role = 'ADMINISTRATOR'"))
        batch_op.create_index('uq_users_active_telegram', ['telegram_user_id'], unique=True, sqlite_where=sa.text('active = 1'))

    op.create_table('defaults',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('pickup_address', sa.Text(), nullable=True),
    sa.Column('courier_id', sa.Integer(), nullable=True),
    sa.Column('customer_id', sa.Integer(), nullable=True),
    sa.Column('waiting_minutes', sa.Integer(), server_default=sa.text('(15)'), nullable=False),
    sa.Column('code_lifetime_minutes', sa.Integer(), server_default=sa.text('(20)'), nullable=False),
    sa.CheckConstraint('code_lifetime_minutes = 20', name=op.f('ck_defaults_code_lifetime')),
    sa.CheckConstraint('id = 1', name=op.f('ck_defaults_singleton')),
    sa.CheckConstraint('waiting_minutes > 0', name=op.f('ck_defaults_positive_wait')),
    sa.ForeignKeyConstraint(['courier_id'], ['users.id'], name=op.f('fk_defaults_courier_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['customer_id'], ['users.id'], name=op.f('fk_defaults_customer_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_defaults'))
    )
    op.create_table('invitations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=255), nullable=False),
    sa.Column('intended_role', sa.Enum('ADMINISTRATOR', 'COURIER', 'CUSTOMER', name='invitation_role', native_enum=False, create_constraint=True), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('revoked', sa.Boolean(create_constraint=True, name='revoked_bool'), server_default=sa.text('0'), nullable=False),
    sa.Column('invited_by_id', sa.Integer(), nullable=False),
    sa.Column('participant_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("intended_role IN ('COURIER', 'CUSTOMER')", name=op.f('ck_invitations_participant_role')),
    sa.CheckConstraint('length(token_hash) > 0', name=op.f('ck_invitations_hash_required')),
    sa.CheckConstraint('length(trim(display_name)) > 0', name=op.f('ck_invitations_name_required')),
    sa.ForeignKeyConstraint(['invited_by_id'], ['users.id'], name=op.f('fk_invitations_invited_by_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['participant_id'], ['users.id'], name=op.f('fk_invitations_participant_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_invitations')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_invitations_token_hash'))
    )
    op.create_table('orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('product_description', sa.Text(), nullable=True),
    sa.Column('scheduled_at', sa.DateTime(), nullable=False),
    sa.Column('waiting_minutes', sa.Integer(), nullable=False),
    sa.Column('pickup_address', sa.Text(), nullable=False),
    sa.Column('courier_id', sa.Integer(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('SCHEDULED', 'COURIER_ARRIVED', 'LOCATION_SUBMITTED', 'PHOTO_SUBMITTED', 'READY_FOR_PICKUP', 'AWAITING_CODE_CONFIRMATION', 'AWAITING_CUSTOMER_CONFIRMATION', 'COMPLETED', 'CUSTOMER_NO_SHOW', 'CANCELLED', 'DISPUTED', name='order_status', native_enum=False, create_constraint=True), server_default='SCHEDULED', nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('arrived_at', sa.DateTime(), nullable=True),
    sa.Column('location_type', sa.String(length=16), nullable=True),
    sa.Column('latitude', sa.Float(), nullable=True),
    sa.Column('longitude', sa.Float(), nullable=True),
    sa.Column('location_url', sa.Text(), nullable=True),
    sa.Column('location_submitted_at', sa.DateTime(), nullable=True),
    sa.Column('photo_file_id', sa.Text(), nullable=True),
    sa.Column('photo_submitted_at', sa.DateTime(), nullable=True),
    sa.Column('ready_at', sa.DateTime(), nullable=True),
    sa.Column('code_verified_at', sa.DateTime(), nullable=True),
    sa.Column('customer_confirmed_at', sa.DateTime(), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(), nullable=True),
    sa.Column('no_show_at', sa.DateTime(), nullable=True),
    sa.Column('disputed_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint("location_type IS NULL OR location_type IN ('TELEGRAM', '2GIS')", name=op.f('ck_orders_location_type')),
    sa.CheckConstraint('courier_id != customer_id', name=op.f('ck_orders_distinct_participants')),
    sa.CheckConstraint('latitude IS NULL OR latitude BETWEEN -90 AND 90', name=op.f('ck_orders_latitude_range')),
    sa.CheckConstraint('length(trim(name)) > 0', name=op.f('ck_orders_name_required')),
    sa.CheckConstraint('length(trim(pickup_address)) > 0', name=op.f('ck_orders_address_required')),
    sa.CheckConstraint('longitude IS NULL OR longitude BETWEEN -180 AND 180', name=op.f('ck_orders_longitude_range')),
    sa.CheckConstraint('waiting_minutes > 0', name=op.f('ck_orders_positive_wait')),
    sa.ForeignKeyConstraint(['courier_id'], ['users.id'], name=op.f('fk_orders_courier_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['customer_id'], ['users.id'], name=op.f('fk_orders_customer_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orders'))
    )
    op.create_table('order_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('occurred_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('actor_telegram_user_id', sa.BigInteger(), nullable=True),
    sa.Column('actor_role', sa.Enum('ADMINISTRATOR', 'COURIER', 'CUSTOMER', name='event_actor_role', native_enum=False, create_constraint=True), nullable=True),
    sa.Column('details', sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_order_events_order_id_orders'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_order_events'))
    )
    with op.batch_alter_table('order_events', schema=None) as batch_op:
        batch_op.create_index('ix_order_events_timeline', ['order_id', 'occurred_at', 'id'], unique=False)

    op.create_table('pickup_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('code_hash', sa.String(length=255), nullable=False),
    sa.Column('generated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('invalidated_at', sa.DateTime(), nullable=True),
    sa.CheckConstraint('expires_at > generated_at', name=op.f('ck_pickup_codes_positive_lifetime')),
    sa.CheckConstraint('length(code_hash) > 0', name=op.f('ck_pickup_codes_hash_required')),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], name=op.f('fk_pickup_codes_order_id_orders'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pickup_codes'))
    )
    with op.batch_alter_table('pickup_codes', schema=None) as batch_op:
        batch_op.create_index('uq_pickup_codes_active_order', ['order_id'], unique=True, sqlite_where=sa.text('used_at IS NULL AND invalidated_at IS NULL'))

    op.execute("INSERT INTO defaults (id) VALUES (1)")
    # Audit rows are immutable even through direct SQL or ORM bulk operations.
    op.execute("""CREATE TRIGGER order_events_no_update BEFORE UPDATE ON order_events
        BEGIN SELECT RAISE(ABORT, 'Order events are immutable'); END""")
    op.execute("""CREATE TRIGGER order_events_no_delete BEFORE DELETE ON order_events
        BEGIN SELECT RAISE(ABORT, 'Order events are immutable'); END""")
    op.execute("""CREATE TRIGGER order_events_no_replace BEFORE INSERT ON order_events
        WHEN EXISTS (SELECT 1 FROM order_events WHERE id = NEW.id)
        BEGIN SELECT RAISE(ABORT, 'Order events are immutable'); END""")
    # Consumption/revocation cannot be reversed to reuse an old credential.
    op.execute("""CREATE TRIGGER invitations_no_reactivation BEFORE UPDATE ON invitations
        WHEN (OLD.used_at IS NOT NULL AND NEW.used_at IS NOT OLD.used_at)
          OR (OLD.revoked = 1 AND NEW.revoked != 1)
          OR NEW.token_hash != OLD.token_hash
        BEGIN SELECT RAISE(ABORT, 'Invitation cannot be reactivated'); END""")
    op.execute("""CREATE TRIGGER pickup_codes_no_reactivation BEFORE UPDATE ON pickup_codes
        WHEN (OLD.used_at IS NOT NULL AND NEW.used_at IS NOT OLD.used_at)
          OR (OLD.invalidated_at IS NOT NULL AND NEW.invalidated_at IS NOT OLD.invalidated_at)
          OR NEW.code_hash != OLD.code_hash OR NEW.order_id != OLD.order_id
          OR NEW.generated_at != OLD.generated_at OR NEW.expires_at != OLD.expires_at
        BEGIN SELECT RAISE(ABORT, 'Pickup code cannot be reactivated'); END""")



def downgrade() -> None:
    op.execute('DROP TRIGGER pickup_codes_no_reactivation')
    op.execute('DROP TRIGGER invitations_no_reactivation')
    op.execute('DROP TRIGGER order_events_no_delete')
    op.execute('DROP TRIGGER order_events_no_replace')
    op.execute('DROP TRIGGER order_events_no_update')
    with op.batch_alter_table('pickup_codes', schema=None) as batch_op:
        batch_op.drop_index('uq_pickup_codes_active_order', sqlite_where=sa.text('used_at IS NULL AND invalidated_at IS NULL'))

    op.drop_table('pickup_codes')
    with op.batch_alter_table('order_events', schema=None) as batch_op:
        batch_op.drop_index('ix_order_events_timeline')

    op.drop_table('order_events')
    op.drop_table('orders')
    op.drop_table('invitations')
    op.drop_table('defaults')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index('uq_users_active_telegram', sqlite_where=sa.text('active = 1'))
        batch_op.drop_index('uq_users_active_administrator', sqlite_where=sa.text("active = 1 AND role = 'ADMINISTRATOR'"))

    op.drop_table('users')
