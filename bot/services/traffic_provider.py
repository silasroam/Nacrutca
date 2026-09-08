"""
Traffic provider service (section 20 — services/traffic_provider.py).

Abstraction over the actual SMM service provider that will fulfil orders
(IVTracker / SOSIAL.servis / custom API, etc.). In a real deployment this
module calls the provider's REST API to submit a job for a paid order.

During staging it just logs the intended submission; the interface is ready
for a real integration. All delivery-side identifiers stay provider-specific.
"""
from __future__ import annotations

import abc
import logging

from ..config import get_settings
from ..database.repository import Repository

logger = logging.getLogger(__name__)


class TrafficProvider(abc.ABC):
    """Base class for order fulfilment providers."""

    name: str = "base"

    async def submit_order(self, order, service) -> str:
        """Submit an order to the provider, returning a provider job id."""
        raise NotImplementedError

    async def check_status(self, provider_job_id: str) -> str:
        raise NotImplementedError


class StubTrafficProvider(TrafficProvider):
    """Stand-in that logs submissions. Swap for a real REST integration."""

    name = "stub"

    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    async def submit_order(self, order, service) -> str:
        # In production: POST to provider with order.target_url, order.quantity,
        # order.service_slug and the order_id as idempotency key.
        logger.info(
            "[%s] submitting order #%s platform=%s service=%s qty=%s url=%s",
            self.name,
            order.order_id,
            order.platform,
            order.service_slug,
            order.quantity,
            order.target_url,
        )
        # Return a fake provider job id.
        return f"job_{order.order_id}"


def get_traffic_provider(repo: Repository) -> TrafficProvider:
    settings = get_settings()
    if settings.use_webhook and settings.webhook_url:
        # future: return RestTrafficProvider(repo) when configured
        pass
    return StubTrafficProvider(repo)