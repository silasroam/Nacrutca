"""Support ticket ORM models.

These are registered on the SAME SQLAlchemy ``Base`` that the Traffic Bot uses,
so both bots share one database and one metadata. The tables are named
``support_tickets`` / ``support_messages`` and are only used by the Support Bot;
the Traffic Bot ignores them. Nothing is deleted on close.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from bot.database.models import Base, utcnow

# Ticket lifecycle (per project spec):
#   user creates  -> waiting_admin
#   admin replies -> waiting_user
#   user replies  -> waiting_admin
#   admin closes  -> closed
STATUS_OPEN = "open"
STATUS_WAITING_ADMIN = "waiting_admin"
STATUS_WAITING_USER = "waiting_user"
STATUS_CLOSED = "closed"

# Who wrote a message in the conversation.
SENDER_USER = "user"
SENDER_ADMIN = "admin"

# Supported Telegram content types stored with each message.
CONTENT_TEXT = "text"
CONTENT_PHOTO = "photo"
CONTENT_DOCUMENT = "document"
CONTENT_VIDEO = "video"


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Human friendly public id e.g. #1047
    ticket_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    # Telegram user id of the ticket owner.
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(255), default="")
    # Order the user attached (optional, auto-resolved from order_id).
    order_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=STATUS_WAITING_ADMIN, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("support_tickets.id"), index=True
    )
    # 'user' or 'admin' — who wrote the message.
    sender_type: Mapped[str] = mapped_column(String(20))
    # Telegram user id (for user) or admin telegram id (for admin).
    sender_id: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    content_type: Mapped[str] = mapped_column(String(20), default=CONTENT_TEXT)
    # The Telegram message_id of the original message in the sender's chat, used
    # to forward photos/documents deterministically; None when not available.
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )