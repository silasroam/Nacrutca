"""
Data access layer (section 20 — database/repository.py).

All DB writes that are financially relevant are kept idempotent:
confirming a payment twice, or processing the same provider webhook twice,
must not double-credit or double-charge (section 23).
"""
from __future__ import annotations

import logging
import random
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from .. import config
from .models import Base, Order, Payment, Service, User, utcnow

logger = logging.getLogger(__name__)


class DuplicatePaymentError(Exception):
    """Raised when an idempotent payment confirmation is repeated."""


class OrderNotFoundError(Exception):
    pass


def _normalize_database_url(url: str) -> str:
    """Force an async driver into the URL where possible.

    The whole storage layer runs on SQLAlchemy *async* engines, so a bare
    ``postgresql://`` URL (or one with a sync-only driver like ``psycopg2``)
    would make ``create_async_engine`` try to import a sync DBAPI module
    (e.g. ``psycopg2``) that is not installed, raising e.g.::

        ModuleNotFoundError: No module named 'psycopg2'

    That unhandled error used to surface as Vercel ``FUNCTION_INVOCATION_FAILED``.
    This normalizer rewrites common sync postgres forms to the async ``asyncpg``
    driver and neutralizes ``channel_binding=require`` (psycopg-only) and
    ``sslmode`` → ``ssl`` so the URL works with ``asyncpg``.
    """
    if not url:
        return url

    # Rewrite drive prefix for PostgreSQL to the async driver.
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    # Drop psycopg2 channel_binding (asyncpg doesn't support it). It can appear
    # anywhere in the query string, so handle all three positions (first/mid/last).
    # Important: handle the "followed by &" form BEFORE the generic "?x" form so
    # a query like `?channel_binding=require&ssl=require` doesn't leave a stray
    # leading `&`.
    url = url.replace("?channel_binding=require&", "?")
    url = url.replace("&channel_binding=require", "")
    url = url.replace("?channel_binding=require", "?")

    # asyncpg uses ssl=..., not sslmode=... .
    url = url.replace("?sslmode=require", "?ssl=require").replace(
        "?sslmode=prefer", "?ssl=prefer"
    )

    return url


class Repository:
    """Async repository wrapping all persistence for the bot."""

    def __init__(self, database_url: str | None = None) -> None:
        url = database_url or config.get_settings().database_url
        url = _normalize_database_url(url)
        # On serverless (Vercel) each request may run on a DIFFERENT asyncio
        # event loop, but the engine/pool is a process-wide singleton reused
        # between requests. asyncpg connections pooled from a previous loop then
        # fail with "attached to a different loop" / "another operation is in
        # progress". NullPool closes every connection after use, so no connection
        # ever outlives the event loop it was created on. (SQLite is unaffected.)
        if url.startswith("postgres"):
            self._engine = create_async_engine(
                url, echo=False, poolclass=NullPool
            )
        else:
            self._engine = create_async_engine(url, echo=False)
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False
        )

    async def ensure_schema(self) -> None:
        """Create all tables if they do not exist yet (idempotent).

        Runs ``Base.metadata.create_all`` through an async engine via
        ``conn.run_sync``. Used on app startup so a cold serverless instance
        never hits a handler before the schema exists.
        """
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database schema ensured via create_all (engine=%s)",
                    self._engine.dialect.name)

    async def init(self) -> None:
        """Ensure schema and run SQLite additive migrations.

        For production (Postgres/Neon) ``create_all`` is enough and idempotent.
        For SQLite we also run lightweight additive migrations so the existing
        `traffic_bot.db` keeps working after new fields are introduced (e.g. the
        crypto invoice columns). New columns are added idempotently.
        """
        await self.ensure_schema()
        if self._engine.dialect.name == "sqlite":
            await self._migrate_sqlite()
            await self._migrate_sqlite_users()
        elif self._engine.dialect.name == "postgresql":
            await self._migrate_postgres_orders()
            await self._migrate_postgres_fsm()
        logger.info("Database schema ensured (engine=%s).", self._engine.dialect.name)

    async def _migrate_sqlite(self) -> None:
        """Add missing columns to the 'orders' table for existing DBs."""
        from sqlalchemy import text
        additions = {
            "crypto_currency": '"VARCHAR(20)"',
            "crypto_amount": '"VARCHAR(64)"',
            "exchange_rate": '"VARCHAR(32)"',
            "fiat_currency": '"VARCHAR(8)"',
            "wallet_address": '"VARCHAR(255)"',
            "expires_at": "DATETIME",
            "paid_at": "DATETIME",
            "transaction_hash": '"VARCHAR(255)"',
            "stars_amount": "INTEGER",
            "telegram_payment_charge_id": '"VARCHAR(255)"',
        }
        async with self._engine.begin() as conn:
            existing = {
                row[1] for row in
                (await conn.execute(text("PRAGMA table_info('orders')"))).fetchall()
            }
            for col, ddl in additions.items():
                if col not in existing:
                    await conn.execute(
                        text(f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
                    )
                    logger.info("Migrated orders.%s (added column)", col)

    async def _migrate_sqlite_users(self) -> None:
        """Add FSM columns to the existing SQLite 'users' table idempotently."""
        from sqlalchemy import text

        async with self._engine.begin() as conn:
            existing = {
                row[1]
                for row in (await conn.execute(text("PRAGMA table_info('users')"))).fetchall()
            }
            for col, ddl in (
                ("state", "VARCHAR(64)"),
                ("draft_json", "TEXT"),
            ):
                if col not in existing:
                    await conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {ddl}"))
                    logger.info("Migrated SQLite users.%s (added column)", col)

    async def _migrate_postgres_fsm(self) -> None:
        """Add FSM columns to the existing Postgres 'users' table idempotently.

        ``create_all`` only creates NEW tables; on an already-existing Neon table
        it does NOT add columns. So we ALTER here to persist per-user FSM state.
        """
        from sqlalchemy import text

        async with self._engine.begin() as conn:
            cols = {
                row[0]
                for row in (
                    await conn.execute(text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='users'"
                    ))
                ).fetchall()
            }
            for col, ddl in (
                ("state", "VARCHAR(64) DEFAULT 'MAIN_MENU'"),
                ("draft_json", "TEXT DEFAULT '{}'"),
            ):
                if col not in cols:
                    await conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {ddl}"))
                    logger.info("Migrated Postgres users.%s (added column)", col)

    async def _migrate_postgres_orders(self) -> None:
        """Add new columns to the existing Postgres 'orders' table idempotently."""
        from sqlalchemy import text

        additions = {}
        async with self._engine.begin() as conn:
            cols = {
                row[0]
                for row in (
                    await conn.execute(text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='orders'"
                    ))
                ).fetchall()
            }
            for col, ddl in additions.items():
                if col not in cols:
                    await conn.execute(text(f"ALTER TABLE orders ADD COLUMN {col} {ddl}"))
                    logger.info("Migrated Postgres orders.%s (added column)", col)

    async def close(self) -> None:
        await self._engine.dispose()

    # ------------------------------------------------------------------
    # Users
    # ------------------------------------------------------------------
    async def upsert_user(
        self, telegram_user_id: int, username: str, first_name: str = ""
    ) -> User:
        async with self._session_factory() as session:
            stmt = select(User).where(User.telegram_user_id == telegram_user_id)
            user = (await session.execute(stmt)).scalar_one_or_none()
            if user is None:
                user = User(
                    telegram_user_id=telegram_user_id,
                    username=username or "",
                    first_name=first_name or "",
                    is_admin=telegram_user_id in config.get_settings().admin_ids,
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
                return user
            user.username = username or user.username
            user.first_name = first_name or user.first_name
            user.is_admin = telegram_user_id in config.get_settings().admin_ids
            await session.commit()
            await session.refresh(user)
            return user

    async def get_user(self, telegram_user_id: int) -> Optional[User]:
        async with self._session_factory() as session:
            stmt = select(User).where(User.telegram_user_id == telegram_user_id)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def list_users(self, limit: int = 50) -> list[User]:
        async with self._session_factory() as session:
            stmt = select(User).order_by(User.created_at.desc()).limit(limit)
            return list((await session.execute(stmt)).scalars().all())

    # ------------------------------------------------------------------
    async def get_fsm(self, telegram_user_id: int) -> tuple[str, dict]:
        """Return (state, draft) persisted for a user, with safe defaults.

        Used as serverless-safe replacement for MemoryPersistence's user_data:
        the interactive step (`state`) and the order draft are read from Postgres.
        """
        async with self._session_factory() as session:
            stmt = select(User).where(User.telegram_user_id == telegram_user_id)
            user = (await session.execute(stmt)).scalar_one_or_none()
            if user is None:
                return OrderState.MAIN_MENU, {}
            draft: dict = {}
            if user.draft_json:
                try:
                    import json as _json

                    draft = _json.loads(user.draft_json) or {}
                except Exception:  # pragma: no cover - malformed stored draft
                    draft = {}
            return (user.state or OrderState.MAIN_MENU), draft

    async def save_fsm(
        self, telegram_user_id: int, state: str, draft: dict | None = None
    ) -> None:
        """Persist (state, draft) for a user. Caller ensures the user row exists."""
        import json as _json

        async with self._session_factory() as session:
            stmt = select(User).where(User.telegram_user_id == telegram_user_id)
            user = (await session.execute(stmt)).scalar_one_or_none()
            if user is None:
                return
            user.state = state or ""
            if draft is not None:
                user.draft_json = _json.dumps(draft, ensure_ascii=False, default=str)
            await session.commit()
    # Services / pricing
    # ------------------------------------------------------------------
    async def list_services(self, platform: str | None = None) -> list[Service]:
        async with self._session_factory() as session:
            stmt = select(Service).order_by(Service.id)
            if platform:
                stmt = stmt.where(Service.platform == platform)
            return list((await session.execute(stmt)).scalars().all())

    async def upsert_service(
        self,
        platform: str,
        slug: str,
        name: str,
        emoji: str,
        price: float,
        requires_url: bool = True,
        is_active: bool = True,
    ) -> Service:
        async with self._session_factory() as session:
            stmt = select(Service).where(
                Service.platform == platform, Service.service_slug == slug
            )
            svc = (await session.execute(stmt)).scalar_one_or_none()
            if svc is None:
                svc = Service(
                    platform=platform,
                    service_slug=slug,
                    name=name,
                    emoji=emoji,
                    price_per_1000=price,
                    requires_url=requires_url,
                    is_active=is_active,
                )
                session.add(svc)
            else:
                svc.name = name
                svc.emoji = emoji
                svc.price_per_1000 = price
                svc.requires_url = requires_url
                svc.is_active = is_active
            await session.commit()
            await session.refresh(svc)
            return svc

    async def get_service(self, service_id: int) -> Optional[Service]:
        async with self._session_factory() as session:
            return await session.get(Service, service_id)

    async def update_service_price(self, service_id: int, price: float) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(Service)
                .where(Service.id == service_id)
                .values(price_per_1000=price, updated_at=utcnow())
            )
            await session.commit()

    async def set_service_active(self, service_id: int, active: bool) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(Service)
                .where(Service.id == service_id)
                .values(is_active=active, updated_at=utcnow())
            )
            await session.commit()
# ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    async def _next_order_id(self, session) -> int:
        """Generate a random order ID between 1 and 10000.

        Checks for collisions to ensure uniqueness.
        """
        for _ in range(100):  # Try up to 100 times to find a unique ID
            candidate = random.randint(1, 10000)
            exists = (
                await session.execute(
                    select(Order.id).where(Order.order_id == candidate)
                )
            ).first()
            if exists is None:
                return candidate
        raise RuntimeError("Could not allocate a unique order ID")

    async def create_order(self, *, tg_user_id, username, platform, service: Service,
                           quantity: int, target_url: str) -> Order:
        async with self._session_factory() as session:
            order_id = await self._next_order_id(session)

            # Authoritative price: re-read from DB, never from the passed object,
            # so a concurrent admin price change is reflected (section 23).
            fresh = await session.get(Service, service.id)
            if fresh is None or not fresh.is_active:
                raise ValueError(f"Service {service.id} not active")
            price_per_1000 = fresh.price_per_1000
            order_service_id = fresh.id

            total = quantity * price_per_1000 / 1000.0
            created = utcnow()
            ttl_seconds = config.get_settings().crypto_invoice_ttl_seconds
            order = Order(
                order_id=order_id,
                telegram_user_id=tg_user_id,
                username=username,
                platform=platform,
                service_name=service.name,
                service_slug=service.service_slug,
                quantity=quantity,
                price_per_1000=price_per_1000,
                total_price=round(total, 2),
                target_url=target_url or "",
                order_status="pending",
                payment_status="pending",
                service_id=order_service_id,
                created_at=created,
                expires_at=created + timedelta(seconds=ttl_seconds),
            )
            session.add(order)
            await session.commit()
            await session.refresh(order)
            return order

    async def get_order_by_id(self, order_id: int) -> Optional[Order]:
        async with self._session_factory() as session:
            stmt = select(Order).where(Order.order_id == order_id)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def get_order_by_db_id(self, db_id: int) -> Optional[Order]:
        async with self._session_factory() as session:
            return await session.get(Order, db_id)

    async def get_order_for_user(self, order_id: int, user_id: int) -> Optional[Order]:
        async with self._session_factory() as session:
            stmt = select(Order).where(
                Order.order_id == order_id, Order.telegram_user_id == user_id
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def user_orders(self, tg_user_id: int, limit: int = 5) -> list[Order]:
        async with self._session_factory() as session:
            stmt = (
                select(Order)
                .where(Order.telegram_user_id == tg_user_id)
                .order_by(Order.created_at.desc())
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars().all())

    async def all_orders(self, limit: int = 50) -> list[Order]:
        async with self._session_factory() as session:
            stmt = (
                select(Order)
                .order_by(Order.created_at.desc())
                .limit(limit)
            )
            return list((await session.execute(stmt)).scalars().all())

    async def set_order_status(self, order_id: int, status: str) -> Optional[Order]:
        async with self._session_factory() as session:
            stmt = select(Order).where(Order.order_id == order_id)
            order = (await session.execute(stmt)).scalar_one_or_none()
            if order is None:
                return None
            order.order_status = status
            await session.commit()
            await session.refresh(order)
            return order

    async def set_payment_status(self, order_id: int, status: str) -> Optional[Order]:
        async with self._session_factory() as session:
            stmt = select(Order).where(Order.order_id == order_id)
            order = (await session.execute(stmt)).scalar_one_or_none()
            if order is None:
                return None
            order.payment_status = status
            await session.commit()
            await session.refresh(order)
            return order

    async def set_payment_fields(self, order_id: int, method: str,
                                 external_id: str) -> Optional[Order]:
        async with self._session_factory() as session:
            stmt = select(Order).where(Order.order_id == order_id)
            order = (await session.execute(stmt)).scalar_one_or_none()
            if order is None:
                return None
            order.payment_method = method
            if external_id:
                order.payment_external_id = external_id
            await session.commit()
            await session.refresh(order)
            return order

    # ------------------------------------------------------------------
    # Crypto invoice lifecycle (section: crypto payment)
    # ------------------------------------------------------------------
    async def save_crypto_invoice(
        self, *, order_id: int, currency: str, crypto_amount: str,
        exchange_rate: str, wallet_address: str,
        expires_at=None, transaction_hash: str = "",
    ) -> Optional[Order]:
        """Persist a freshly created crypto invoice on an order.

        The exchange rate / crypto amount are frozen at creation time and never
        auto-updated afterwards (section: "no re-pricing after invoice").
        `expires_at` defaults to the order's already-set deadline (set at order
        creation) and is only overridden when a value is explicitly passed.
        """
        async with self._session_factory() as session:
            stmt = select(Order).where(Order.order_id == order_id)
            order = (await session.execute(stmt)).scalar_one_or_none()
            if order is None:
                return None
            order.payment_method = "crypto"
            order.crypto_currency = currency
            order.crypto_amount = crypto_amount
            order.exchange_rate = exchange_rate
            order.fiat_currency = "RUB"
            order.wallet_address = wallet_address
            if expires_at is not None:
                order.expires_at = expires_at
            if transaction_hash:
                order.transaction_hash = transaction_hash
            await session.commit()
            await session.refresh(order)
            return order

    async def is_transaction_used(self, transaction_hash: str) -> bool:
        """Idempotency guard: a single on-chain tx hash must never be applied
        to more than one order (section: duplicate-payment protection)."""
        if not transaction_hash:
            return False
        async with self._session_factory() as session:
            stmt = select(Order).where(
                Order.transaction_hash == transaction_hash,
                Order.payment_status == "paid",
            )
            return (await session.execute(stmt)).first() is not None

    async def confirm_crypto_payment(self, order_id: int, tx_hash: str,
                                     amount: str) -> Optional[Order]:
        """Commit a verified on-chain transaction as the paid invoice.

        Idempotent: if the order is already paid with the same tx hash, returns
        it untouched. Never marks an order paid from client data."""
        async with self._session_factory() as session:
            stmt = select(Order).where(Order.order_id == order_id)
            order = (await session.execute(stmt)).scalar_one_or_none()
            if order is None:
                return None
            if order.payment_status == "paid":
                return order
            order.payment_status = "paid"
            order.transaction_hash = tx_hash
            order.paid_at = utcnow()
            if order.order_status == "pending":
                order.order_status = "processing"
            await session.commit()
            await session.refresh(order)
            return order

    async def cancel_crypto_order(self, order_id: int) -> Optional[Order]:
        """Cancel a pending crypto order (user pressed "Отменить")."""
        async with self._session_factory() as session:
            stmt = select(Order).where(Order.order_id == order_id)
            order = (await session.execute(stmt)).scalar_one_or_none()
            if order is None:
                return None
            if order.payment_status in ("paid", "cancelled"):
                return order
            order.payment_status = "cancelled"
            order.order_status = "cancelled"
            await session.commit()
            await session.refresh(order)
            return order

    # ------------------------------------------------------------------
    # Telegram Stars (idempotent)
    # ------------------------------------------------------------------
    async def set_stars_amount(self, order_id: int, stars_amount: int) -> Optional[Order]:
        """Persist the computed whole-number Stars total on the order."""
        async with self._session_factory() as session:
            stmt = select(Order).where(Order.order_id == order_id)
            order = (await session.execute(stmt)).scalar_one_or_none()
            if order is None:
                return None
            order.payment_method = "stars"
            order.stars_amount = int(stars_amount)
            await session.commit()
            await session.refresh(order)
            return order

    async def is_charge_used(self, charge_id: str) -> bool:
        """Idempotency guard: one Telegram charge must not pay two orders."""
        if not charge_id:
            return False
        async with self._session_factory() as session:
            stmt = select(Order).where(
                Order.telegram_payment_charge_id == charge_id,
                Order.payment_status == "paid",
            )
            return (await session.execute(stmt)).first() is not None

    async def confirm_stars_payment(self, order_id: int, charge_id: str) -> Optional[Order]:
        """Commit a verified Telegram Stars payment for an order.

        Idempotent: if the order is already paid (same charge) it returns it
        untouched. Never marks an order paid from a button press - only from the
        real Telegram successful_payment update.
        """
        async with self._session_factory() as session:
            stmt = select(Order).where(Order.order_id == order_id)
            order = (await session.execute(stmt)).scalar_one_or_none()
            if order is None:
                return None
            if order.payment_status == "paid":
                return order
            order.payment_status = "paid"
            order.telegram_payment_charge_id = charge_id
            order.paid_at = utcnow()
            if order.order_status == "pending":
                order.order_status = "processing"
            await session.commit()
            await session.refresh(order)
            return order

    # ------------------------------------------------------------------
    # Payments (idempotent)
    # ------------------------------------------------------------------
    @staticmethod
    def new_external_id() -> str:
        return "tr_" + secrets.token_hex(12)

    async def create_payment(
        self, *, order_id, tg_user_id, method, provider, amount, payload=""
    ) -> Payment:
        async with self._session_factory() as session:
            payment = Payment(
                payment_id=self.new_external_id(),
                order_id=order_id,
                telegram_user_id=tg_user_id,
                method=method,
                provider=provider,
                amount=amount,
                status="pending",
                provider_payload=payload,
            )
            session.add(payment)
            await session.commit()
            await session.refresh(payment)
            return payment

    async def get_payment(self, payment_id: str) -> Optional[Payment]:
        async with self._session_factory() as session:
            stmt = select(Payment).where(Payment.payment_id == payment_id)
            return (await session.execute(stmt)).scalar_one_or_none()

    async def get_existing_payment(self, order_id: int, method: str,
                                   statuses: list[str]) -> Optional[Payment]:
        async with self._session_factory() as session:
            stmt = select(Payment).where(
                Payment.order_id == order_id,
                Payment.method == method,
                Payment.status.in_(statuses),
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def confirm_payment(self, payment_id: str, order_id: int) -> Payment:
        """Idempotently mark a payment + its order as paid.

        If the order is already paid via the same payment, this is a no-op.
        If the order was already paid by a *different* payment, the new one is
        rejected with DuplicatePaymentError (section 23 idempotency).
        """
        async with self._session_factory() as session:
            payment = (
                await session.execute(
                    select(Payment).where(Payment.payment_id == payment_id)
                )
            ).scalar_one_or_none()
            if payment is None:
                raise OrderNotFoundError("payment not found")

            if payment.status == "paid":
                logger.info("Idempotent confirm: payment %s already paid", payment_id)
                return payment

            stmt = select(Order).where(Order.order_id == order_id)
            order = (await session.execute(stmt)).scalar_one_or_none()
            if order is None:
                raise OrderNotFoundError("order not found")

            if order.payment_status == "paid":
                logger.warning(
                    "Order %s already paid via payment %s",
                    order_id, order.payment_external_id,
                )
                payment.status = "failed"
                await session.commit()
                raise DuplicatePaymentError(
                    f"order {order_id} already paid via another payment"
                )

            payment.status = "paid"
            order.payment_status = "paid"
            order.payment_method = payment.method
            order.payment_external_id = payment.payment_id
            # On successful payment the order automatically moves to processing.
            if order.order_status == "pending":
                order.order_status = "processing"

            await session.commit()
            await session.refresh(payment)
            logger.info("Payment %s confirmed, order %s -> paid", payment_id, order_id)
            return payment

    # ------------------------------------------------------------------
    # Statistics (section 16)
    # ------------------------------------------------------------------
    async def user_stats(self, tg_user_id: int, days: int | None = None,
                         completed_only: bool = False) -> dict:
        async with self._session_factory() as session:
            stmt = select(Order).where(Order.telegram_user_id == tg_user_id)
            if days is not None:
                since = datetime.now(timezone.utc) - timedelta(days=days)
                stmt = stmt.where(Order.created_at >= since)
            orders = list((await session.execute(stmt)).scalars().all())

            # "Моя статистика" counts only fully completed orders. Filtering is
            # display-only: nothing is deleted or re-stated in the DB.
            if completed_only:
                orders = [o for o in orders if o.order_status == "completed"]

            total_orders = len(orders)
            completed = sum(1 for o in orders if o.order_status == "completed")
            processing = sum(
                1 for o in orders if o.order_status in ("processing", "pending")
            )
            cancelled = sum(1 for o in orders if o.order_status == "cancelled")
            if completed_only:
                spent = sum(o.total_price for o in orders)
                units = sum(o.quantity for o in orders)
            else:
                spent = sum(o.total_price for o in orders if o.payment_status == "paid")
                units = sum(o.quantity for o in orders if o.payment_status == "paid")
            return {
                "total_orders": total_orders,
                "completed": completed,
                "processing": processing,
                "cancelled": cancelled,
                "spent": round(spent, 2),
                "units": units,
            }

    async def sales_stats(self, days: int | None = None) -> dict:
        async with self._session_factory() as session:
            stmt = select(Order)
            if days is not None:
                since = datetime.now(timezone.utc) - timedelta(days=days)
                stmt = stmt.where(Order.created_at >= since)
            orders = list((await session.execute(stmt)).scalars().all())
            paid = [o for o in orders if o.payment_status == "paid"]
            return {
                "orders": len(orders),
                "paid": len(paid),
                "revenue": round(sum(o.total_price for o in paid), 2),
                "units": sum(o.quantity for o in paid),
            }