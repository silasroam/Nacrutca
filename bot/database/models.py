"""SQLAlchemy ORM models (section 20 — database/models.py)."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    """A Telegram user who interacts with the bot."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(255), default="")
    first_name: Mapped[str] = mapped_column(String(255), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Service(Base):
    """A sellable service unit (platform + service + price)."""

    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("platform", "service_slug", name="uq_platform_service"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(50), index=True)
    service_slug: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(100))
    emoji: Mapped[str] = mapped_column(String(10), default="")
    price_per_1000: Mapped[float] = mapped_column(Float, default=0.0)
    min_quantity: Mapped[int] = mapped_column(Integer, default=None, nullable=True)
    max_quantity: Mapped[int] = mapped_column(Integer, default=None, nullable=True)
    requires_url: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    orders: Mapped[list["Order"]] = relationship(back_populates="service")


class Order(Base):
    """A single purchase order (section 12)."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Human friendly public id e.g. #1842 -> stored as integer 1842
    order_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, index=True)
    username: Mapped[str] = mapped_column(String(255), default="")

    platform: Mapped[str] = mapped_column(String(50))
    service_name: Mapped[str] = mapped_column(String(100))
    service_slug: Mapped[str] = mapped_column(String(50))

    quantity: Mapped[int] = mapped_column(Integer)
    price_per_1000: Mapped[float] = mapped_column(Float)
    total_price: Mapped[float] = mapped_column(Float)

    payment_method: Mapped[str] = mapped_column(String(20), default="")   # crypto | stars
    payment_status: Mapped[str] = mapped_column(String(20), default="pending")
    order_status: Mapped[str] = mapped_column(String(20), default="pending")

    target_url: Mapped[str] = mapped_column(Text, default="")
    payment_external_id: Mapped[str] = mapped_column(String(255), default="")

    # --- crypto payment fields (section: crypto invoice) ---
    crypto_currency: Mapped[str] = mapped_column(String(20), default="")  # TRX|LTC|TON|SOL
    crypto_amount: Mapped[str] = mapped_column(String(64), default="")    # exact Decimal as str
    exchange_rate: Mapped[str] = mapped_column(String(32), default="")    # RUB per 1 coin as str
    fiat_currency: Mapped[str] = mapped_column(String(8), default="RUB")
    wallet_address: Mapped[str] = mapped_column(String(255), default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transaction_hash: Mapped[str] = mapped_column(String(255), default="")

    # --- Telegram Stars payment fields ---
    stars_amount: Mapped[int] = mapped_column(Integer, default=0)     # whole ⭐ count
    telegram_payment_charge_id: Mapped[str] = mapped_column(String(255), default="")

    service_id: Mapped[int | None] = mapped_column(
        ForeignKey("services.id"), nullable=True
    )
    service: Mapped["Service | None"] = relationship(back_populates="orders")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Payment(Base):
    """A payment attempt record. Keeps financial operations traceable."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    order_id: Mapped[int] = mapped_column(Integer, index=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, index=True)
    method: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(50))
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    provider_payload: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )