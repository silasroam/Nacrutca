"""
Order service (section 20 — services/orders.py).

Encapsulates business logic for building an order from a valid quantity and
service, keeps quantity/price decoupled from the client, and validates limits.
"""
from __future__ import annotations

import logging

from ..config import Settings, get_settings
from ..database.repository import Repository
from ..database.models import Order, Service
from .pricing import calculate_total

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Raised when user-provided data fails validation."""


def validate_quantity(raw: str, settings: Settings, svc: Service | None) -> int:
    """Validate + return positive integer quantity within limits.

    Raises ValidationError with a user-friendly message on failure.
    """
    stripped = raw.strip().replace(" ", "").replace("\u2009", "").replace(",", "")
    if not stripped.isdigit():
        raise ValidationError("Некорректное количество. Введите целое число.")

    try:
        quantity = int(stripped)
    except ValueError:
        raise ValidationError("Некорректное количество. Введите целое число.")

    if quantity <= 0:
        raise ValidationError("Количество должно быть больше нуля.")

    min_q = svc.min_quantity if (svc and svc.min_quantity) else settings.min_quantity
    max_q = svc.max_quantity if (svc and svc.max_quantity) else settings.max_quantity

    if quantity < min_q:
        raise ValidationError(
            f"Минимальное количество — {min_q:,}.".replace(",", " ")
        )
    if quantity > max_q:
        raise ValidationError(
            f"Максимальное количество — {max_q:,}.".replace(",", " ")
        )
    return quantity


def build_total(svc: Service, quantity: int) -> float:
    """Server-side authoritative total. Client never supplies a price."""
    return calculate_total(svc.price_per_1000, quantity)


async def place_order(repo: Repository, *, tg_user_id, username, platform,
                      svc: Service, quantity: int, target_url: str) -> Order:
    """Persist a new order with a server-computed price."""
    return await repo.create_order(
        tg_user_id=tg_user_id,
        username=username,
        platform=platform,
        service=svc,
        quantity=quantity,
        target_url=target_url,
    )


def fmt_price(value: float) -> str:
    """Nicely format a price, e.g. 1450.0 -> '1 450'."""
    text = f"{value:,.0f}"
    return text.replace(",", " ")


def fmt_number(value: int) -> str:
    return f"{value:,.0f}".replace(",", " ")


def fmt_rate_1k(price: float) -> str:
    """Compact per-1000 rate used in the service list: '300 ₽ / 1K'."""
    return f"{fmt_price(price)} ₽ / 1K"


def fmt_rate_1000(price: float) -> str:
    """Detailed per-1000 rate used in cards: '300 ₽ / 1 000'."""
    return f"{fmt_price(price)} ₽ / 1 000"


def order_code(order) -> str:
    """Safe user-facing order code.

    Uses the internal order_id as the user-facing code (e.g. #1234).
    The public_hash column was removed from production — this function
    gracefully falls back to order_id for any row.
    """
    return f"#{getattr(order, 'order_id', '?')}"