"""
End-to-end regression test for the crypto currency selection flow.

Verifies that a freshly created pending order is NOT reported as "Заказ устарел"
when the user picks any of the four currencies, and that a real invoice is shown
(covers the root cause: `pending_order_id` / `order_id` now set before branching,
and `expires_at` set at order creation).
"""
import asyncio
from decimal import Decimal

from bot.database.repository import Repository
from bot.services import crypto_rates as rates
from bot.services import pricing
from bot.handlers import payment as payment_h


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answered = False

    async def answer(self, **kwargs):
        self.answered = True


class FakeUser:
    id = 555
    username = "buyer"


class FakeChat:
    id = -100555


class FakeUpdate:
    def __init__(self, data):
        self.callback_query = FakeQuery(data)
        self.effective_user = FakeUser()
        self.effective_chat = FakeChat()


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append(text)
        return None


class FakeContext:
    def __init__(self, repo):
        self.user_data = {}
        self.bot_data = {"app": type("App", (), {"repo": repo})()}
        self.bot = FakeBot()


async def main() -> None:
    r = Repository("sqlite+aiosqlite:///:memory:")
    await r.init()
    await pricing.seed_default_services(r)

    # Freeze CoinGecko rate -> deterministic invoice math (500/119.87 etc.)
    original_get_rate = rates.get_rate
    async def _fake_get_rate(code):  # noqa: ANN001
        return _fake_rate(code)
    rates.get_rate = _fake_get_rate  # type: ignore[assignment]

    try:
        for _ in range(4):
            likes = next(
                s for s in await r.list_services("tiktok") if s.service_slug == "likes"
            )
            order = await r.create_order(
                tg_user_id=555, username="buyer", platform="tiktok", service=likes,
                quantity=1000, target_url="https://tiktok.com/@x/video/1",
            )
            ctx = FakeContext(r)
            # Simulate what pay_method_selected does BEFORE showing currencies:
            ctx.user_data["pending_order_id"] = order.order_id
            ctx.user_data["draft"] = {
                "order_id": order.order_id,
                "service": likes,
                "quantity": 1000,
                "platform": "tiktok",
            }

            for code in ("TRX", "LTC", "TON", "SOL"):
                update = FakeUpdate(f"cb:crypto:w:{code}:{order.order_id}")
                await payment_h.crypto_wallet_selected(update, ctx)
                last = ctx.bot.messages[-1] if ctx.bot.messages else ""
                assert "Заказ устарел" not in last, (
                    f"{code}: fresh order falsely expired!"
                )
                assert "Сумма к оплате" in last and "Адрес для оплаты" in last, (
                    f"{code}: invoice not shown: {last[:80]}"
                )
                print(f"{code}: invoice OK (no stale-order error)")
    finally:
        rates.get_rate = original_get_rate  # type: ignore[assignment]

    await r.close()
    print("CRYPTO FLOW TEST PASSED")


def _fake_rate(code: str) -> Decimal:
    fake = {"TRX": "29.2", "LTC": "4762.48", "TON": "119.86", "SOL": "8857.57"}
    return Decimal(fake[code])


if __name__ == "__main__":
    asyncio.run(main())