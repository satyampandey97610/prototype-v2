"""Initial schema for MicroHeal Care Concierge

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-11-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision: str = "001_initial_schema"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "societies",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("city", sa.String(length=100), nullable=False),
        sa.Column("address", sa.String(length=300), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("annex_doc_id", sa.String(length=128), nullable=False),
        sa.Column("hospital", sa.JSON(), nullable=False),
    )

    op.create_table(
        "clinicians",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("service", sa.String(length=64), nullable=False),
        sa.Column("subtypes", sa.JSON(), nullable=False),
        sa.Column("hospital", sa.String(length=200), nullable=False),
        sa.Column("slot_minutes", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("availability", sa.JSON(), nullable=False),
    )

    op.create_table(
        "patients",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("society_id", sa.String(length=64), sa.ForeignKey("societies.id"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False, server_default="en"),
        sa.Column("date_of_birth", sa.String(length=32), nullable=False),
        sa.Column("gender", sa.String(length=16), nullable=False),
        sa.Column("flat", sa.String(length=64), nullable=False),
    )
    op.create_index("ix_patients_society_id", "patients", ["society_id"])

    op.create_table(
        "corpus_chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("chunk_id", sa.String(length=200), nullable=False, unique=True),
        sa.Column("doc_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("section", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("applies_to", sa.String(length=128), nullable=False, server_default="all"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("effective_from", sa.String(length=32), nullable=True),
        sa.Column("superseded_by", sa.String(length=128), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
    )
    op.create_index("ix_corpus_chunks_chunk_id", "corpus_chunks", ["chunk_id"])
    op.create_index("ix_corpus_chunks_doc_id", "corpus_chunks", ["doc_id"])
    op.create_index("ix_corpus_chunks_applies_to", "corpus_chunks", ["applies_to"])

    op.create_table(
        "bookings",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column("patient_id", sa.String(length=64), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("society_id", sa.String(length=64), sa.ForeignKey("societies.id"), nullable=False),
        sa.Column("clinician_id", sa.String(length=64), sa.ForeignKey("clinicians.id"), nullable=False),
        sa.Column("service", sa.String(length=64), nullable=False),
        sa.Column("subtype", sa.String(length=128), nullable=False),
        sa.Column("slot_start", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="CONFIRMED"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", name="uq_bookings_request_id"),
    )
    op.create_index("ix_bookings_request_id", "bookings", ["request_id"])
    op.create_index("ix_bookings_thread_id", "bookings", ["thread_id"])
    op.create_index("ix_bookings_patient_id", "bookings", ["patient_id"])
    op.create_index("ix_bookings_society_id", "bookings", ["society_id"])
    op.create_index("ix_bookings_clinician_id", "bookings", ["clinician_id"])
    op.create_index("ix_bookings_slot_start", "bookings", ["slot_start"])

    op.create_table(
        "escalations",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("thread_id", sa.String(length=128), nullable=False),
        sa.Column("patient_id", sa.String(length=64), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("society_id", sa.String(length=64), sa.ForeignKey("societies.id"), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("sla_minutes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="OPEN"),
        sa.Column("trigger_message", sa.Text(), nullable=False),
        sa.Column("emergency_contact", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_escalations_thread_id", "escalations", ["thread_id"])
    op.create_index("ix_escalations_patient_id", "escalations", ["patient_id"])
    op.create_index("ix_escalations_society_id", "escalations", ["society_id"])

    op.create_table(
        "conversation_threads",
        sa.Column("thread_id", sa.String(length=128), primary_key=True, nullable=False),
        sa.Column("patient_id", sa.String(length=64), sa.ForeignKey("patients.id"), nullable=False),
        sa.Column("society_id", sa.String(length=64), sa.ForeignKey("societies.id"), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_conversation_threads_patient_id", "conversation_threads", ["patient_id"])
    op.create_index("ix_conversation_threads_society_id", "conversation_threads", ["society_id"])

    op.create_table(
        "thread_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column(
            "thread_id",
            sa.String(length=128),
            sa.ForeignKey("conversation_threads.thread_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_thread_messages_thread_id", "thread_messages", ["thread_id"])


def downgrade() -> None:
    op.drop_table("thread_messages")
    op.drop_table("conversation_threads")
    op.drop_table("escalations")
    op.drop_table("bookings")
    op.drop_table("corpus_chunks")
    op.drop_table("patients")
    op.drop_table("clinicians")
    op.drop_table("societies")
