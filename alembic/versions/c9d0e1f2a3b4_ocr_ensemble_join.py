"""Hold OCR readings from several hosts until they can be joined.

An image is now sent to EVERY OCR host rather than to whichever worker pops it
first, because the engines fail differently and the LLM reconciles them better
than any one of them reads. Apple Vision on the Mac read "2 Has butter melted";
rapidocr on Linux read "Hhsbuuenmelten"; together the model resolves
"2 tablespoons butter, melted", which neither produced alone.

That turns one completion into several, so the readings need somewhere to
accumulate between them.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""
from alembic import op
import sqlalchemy as sa

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # One entry per host that answered: {queue, provider, text, meta}.
    op.add_column("recipe_ingestions", sa.Column("ocr_readings", sa.JSON(), nullable=True))
    # How many hosts were asked, recorded at fan-out time rather than read from
    # config at join time -- config can change while a job is in flight, and a
    # join that waits for a host nobody sent to never completes.
    op.add_column("recipe_ingestions", sa.Column("ocr_expected", sa.Integer(), nullable=True))
    # The claim. Both the last arriving reading and the deadline timer race to
    # continue the pipeline; whichever sets this first wins, and the other stops.
    op.add_column("recipe_ingestions", sa.Column("ocr_joined_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("recipe_ingestions", "ocr_joined_at")
    op.drop_column("recipe_ingestions", "ocr_expected")
    op.drop_column("recipe_ingestions", "ocr_readings")
