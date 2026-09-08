"""Data access for the Support Bot.

Shares the EXACT same database as the Traffic Bot (DATABASE_URL) and reuses the
existing User/Order models read-only — there is no second copy of users or
orders. Tickets and messages live in new dedicated tables created idempotently.
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from bot.database.models import Order, User, utcnow

from . import models as support_models  # noqa: F401  (register tables on Base)
from .models import SupportMessage, SupportTicket

logger = logging.getLogger(__name__)

# Statuses the admin panel treats as "active / requires attention".
ACTIVE_STATUSES = ("waiting_admin", "waiting_user")


class SupportRepository:
    """Async repository for support tickets + read access to shared tables."""

    def __init__(self, database_url: str) -> None:
        self._engine = create_async_engine(database_url, echo=False)
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False
        )

    async def init(self) -> None:
        from bot.database.models import Base
        async with self._engine.begin() as conn:
            # support models are imported above, so metadata includes the new
            # tables; existing traffic tables are untouched.
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Support database schema ensured.")

    async def close(self) -> None:
        await self._engine.dispose()

    # ------------------------------------------------------------------
    # Shared User/Order access (read-only, no second copy)
    # ------------------------------------------------------------------
    async def get_user(self, telegram_user_id: int) -> User | None:
        async with self._session_factory() as session:
            return (
                await session.execute(
                    select(User).where(User.telegram_user_id == telegram_user_id)
                )
            ).scalar_one_or_none()

    async def upsert_user(self, telegram_user_id: int, username: str,
                          first_name: str = "") -> None:
        async with self._session_factory() as session:
            found = (
                await session.execute(
                    select(User).where(User.telegram_user_id == telegram_user_id)
                )
            ).scalar_one_or_none()
            if found is None:
                found = User(
                    telegram_user_id=telegram_user_id,
                    username=username or "",
                    first_name=first_name or "",
                )
                session.add(found)
            else:
                if username:
                    found.username = username
                if first_name:
                    found.first_name = first_name
            await session.commit()

    async def find_order_by_id(self, order_id: int) -> Order | None:
        async with self._session_factory() as session:
            return (
                await session.execute(
                    select(Order).where(Order.order_id == order_id)
                )
            ).scalar_one_or_none()

    # ------------------------------------------------------------------
    # Tickets
    # ------------------------------------------------------------------
    @staticmethod
    def _next_ticket_id(max_id: int | None) -> int:
        base = 1000
        return (max_id or base - 1) + 1

    async def create_ticket(self, *, user_id: int, username: str,
                            order_id: int | None, text: str,
                            content_type: str,
                            telegram_message_id: int | None) -> SupportTicket:
        async with self._session_factory() as session:
            max_id = (
                await session.execute(select(func.max(SupportTicket.ticket_id)))
            ).scalar()
            ticket_id = self._next_ticket_id(max_id)
            ticket = SupportTicket(
                ticket_id=ticket_id,
                user_id=user_id,
                username=username or "",
                order_id=order_id,
                status="waiting_admin",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            session.add(ticket)
            await session.flush()  # obtain ticket.id
            session.add(
                SupportMessage(
                    ticket_id=ticket.id,
                    sender_type="user",
                    sender_id=user_id,
                    text=text or "",
                    content_type=content_type,
                    telegram_message_id=telegram_message_id,
                )
            )
            await session.commit()
            await session.refresh(ticket)
            return ticket

    async def add_message(self, ticket_pk: int, *, sender_type: str,
                          sender_id: int, text: str, content_type: str,
                          telegram_message_id: int | None) -> SupportMessage:
        async with self._session_factory() as session:
            msg = SupportMessage(
                ticket_id=ticket_pk,
                sender_type=sender_type,
                sender_id=sender_id,
                text=text or "",
                content_type=content_type,
                telegram_message_id=telegram_message_id,
            )
            session.add(msg)
            ticket = await session.get(SupportTicket, ticket_pk)
            if ticket is not None:
                ticket.updated_at = utcnow()
            await session.commit()
            await session.refresh(msg)
            return msg

    async def get_ticket_by_id(self, ticket_id: int) -> SupportTicket | None:
        async with self._session_factory() as session:
            return (
                await session.execute(
                    select(SupportTicket).where(
                        SupportTicket.ticket_id == ticket_id
                    )
                )
            ).scalar_one_or_none()

    async def get_ticket_by_pk(self, pk: int) -> SupportTicket | None:
        async with self._session_factory() as session:
            return await session.get(SupportTicket, pk)

    async def set_ticket_status(self, ticket_id: int, status: str) -> SupportTicket | None:
        async with self._session_factory() as session:
            ticket = (
                await session.execute(
                    select(SupportTicket).where(
                        SupportTicket.ticket_id == ticket_id
                    )
                )
            ).scalar_one_or_none()
            if ticket is None:
                return None
            ticket.status = status
            ticket.updated_at = utcnow()
            await session.commit()
            await session.refresh(ticket)
            return ticket

    async def list_user_tickets(self, user_id: int, limit: int = 10) -> list[SupportTicket]:
        async with self._session_factory() as session:
            stmt = (
                select(SupportTicket)
                .where(SupportTicket.user_id == user_id)
                .order_by(SupportTicket.created_at.desc())
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars().all())

    async def list_active_tickets(self, limit: int = 20) -> list[SupportTicket]:
        async with self._session_factory() as session:
            stmt = (
                select(SupportTicket)
                .where(SupportTicket.status.in_(ACTIVE_STATUSES))
                .order_by(SupportTicket.created_at.asc())
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars().all())

    async def list_tickets_by_status(self, status: str, limit: int = 20) -> list[SupportTicket]:
        async with self._session_factory() as session:
            stmt = (
                select(SupportTicket)
                .where(SupportTicket.status == status)
                .order_by(SupportTicket.updated_at.desc())
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars().all())

    async def ticket_messages(self, ticket_pk: int) -> list[SupportMessage]:
        async with self._session_factory() as session:
            stmt = (
                select(SupportMessage)
                .where(SupportMessage.ticket_id == ticket_pk)
                .order_by(SupportMessage.created_at.asc(), SupportMessage.id.asc())
            )
            return list((await session.execute(stmt)).scalars().all())

    # Most recent non-closed ticket for a user, used to route plain answers.
    async def latest_open_ticket(self, user_id: int) -> SupportTicket | None:
        async with self._session_factory() as session:
            stmt = (
                select(SupportTicket)
                .where(
                    SupportTicket.user_id == user_id,
                    SupportTicket.status.in_(("waiting_admin", "waiting_user")),
                )
                .order_by(SupportTicket.updated_at.desc())
                .limit(1)
            )
            return (await session.execute(stmt)).scalar_one_or_none()