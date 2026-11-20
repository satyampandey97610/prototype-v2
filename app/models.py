from datetime import datetime, timezone
from typing import Any
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, orm
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Society(Base):
    __tablename__ = "societies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    address: Mapped[str] = mapped_column(String(300), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", nullable=False)
    annex_doc_id: Mapped[str] = mapped_column(String(128), nullable=False)
    hospital: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    patients: Mapped[list["Patient"]] = relationship("Patient", back_populates="society")


class Clinician(Base):
    __tablename__ = "clinicians"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    service: Mapped[str] = mapped_column(String(64), nullable=False)
    subtypes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    hospital: Mapped[str] = mapped_column(String(200), nullable=False)
    slot_minutes: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    availability: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    society_id: Mapped[str] = mapped_column(ForeignKey("societies.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(String(32), default="en", nullable=False)
    date_of_birth: Mapped[str] = mapped_column(String(32), nullable=False)
    gender: Mapped[str] = mapped_column(String(16), nullable=False)
    flat: Mapped[str] = mapped_column(String(64), nullable=False)

    society: Mapped["Society"] = relationship("Society", back_populates="patients")


class CorpusChunk(Base):
    __tablename__ = "corpus_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chunk_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    doc_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    section: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    applies_to: Mapped[str] = mapped_column(String(128), default="all", nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    effective_from: Mapped[str | None] = mapped_column(String(32), nullable=True)
    superseded_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_bookings_request_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    society_id: Mapped[str] = mapped_column(ForeignKey("societies.id"), nullable=False, index=True)
    clinician_id: Mapped[str] = mapped_column(ForeignKey("clinicians.id"), nullable=False, index=True)
    service: Mapped[str] = mapped_column(String(64), nullable=False)
    subtype: Mapped[str] = mapped_column(String(128), nullable=False)
    slot_start: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="CONFIRMED", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    scheduled_for = orm.synonym("slot_start")


class Escalation(Base):
    __tablename__ = "escalations"
    __table_args__ = (
        CheckConstraint("level >= 1 AND level <= 5", name="ck_escalations_level_1_5"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    society_id: Mapped[str] = mapped_column(ForeignKey("societies.id"), nullable=False, index=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    sla_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="OPEN", nullable=False)
    trigger_message: Mapped[str] = mapped_column(Text, nullable=False)
    emergency_contact: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    def __init__(self, **kwargs: Any):
        symptom = kwargs.pop("symptom", None)
        severity = kwargs.pop("severity", None)
        request_id = kwargs.pop("request_id", None)

        if "thread_id" not in kwargs or kwargs["thread_id"] is None:
            kwargs["thread_id"] = request_id or "symptom_log_thread"
        if "level" not in kwargs or kwargs["level"] is None:
            if severity is not None:
                kwargs["level"] = severity
            else:
                kwargs["level"] = 3
        if "sla_minutes" not in kwargs or kwargs["sla_minutes"] is None:
            lvl = kwargs["level"]
            kwargs["sla_minutes"] = 5 if lvl == 1 else (30 if lvl == 2 else 240)
        if "trigger_message" not in kwargs or kwargs["trigger_message"] is None:
            kwargs["trigger_message"] = symptom or ""

        super().__init__(**kwargs)
        self.symptom = symptom
        self.severity = severity if severity is not None else self.level
        self.request_id = request_id


class ConversationThread(Base):
    __tablename__ = "conversation_threads"

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    society_id: Mapped[str] = mapped_column(ForeignKey("societies.id"), nullable=False, index=True)
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    messages: Mapped[list["ThreadMessage"]] = relationship(
        "ThreadMessage",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="ThreadMessage.id",
    )


class ThreadMessage(Base):
    __tablename__ = "thread_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    thread: Mapped["ConversationThread"] = relationship("ConversationThread", back_populates="messages")


DocChunk = CorpusChunk
ServiceRequest = Booking
SymptomLog = Escalation