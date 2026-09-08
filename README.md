# 🚀 Traffic Bot

Production-oriented Telegram bot for selling social-media promotion traffic
(subscribers, views, likes, comments, reposts) across **Telegram**, **TikTok**,
**Instagram** and **YouTube**. Payments are accepted in **cryptocurrency** and
**Telegram Stars**.

Built with a clean, modular architecture: FSM-based navigation, an idempotent
payment layer with a provider abstraction, a server-authoritative pricing
model, and a full admin panel.

---

## Features

- **Simple modern commercial UI** driven by inline buttons (single sequential flow).
- **Server-side pricing** — the client can never supply a price; totals are
  always recomputed from the `services` table.
- **Idempotent payments** — webhook re-delivery and duplicate confirmations are
  guarded; an order is only marked `paid` via provider confirmation, never from
  a button press.
- **FSM navigation** for every screen with a consistent `🔙 Назад` step.
- **Validation** for quantity, URL and all untrusted inputs (section 23/24/25).
- **Admin panel** to view orders/users/payments, change prices, toggle services,
  edit order statuses and see sales statistics without touching handler code.
- **PaymentProvider abstraction** (`CryptoPaymentProvider`,
  `TelegramStarsProvider`) so business logic never depends on the concrete
  gateway.
- **TrafficProvider abstraction** as the integration point for the real SMM API.

---

## Project structure

```
project/
├── bot/
│   ├── config.py               # env config (secrets stay out of source)
│   ├── main.py                 # entry point (polling/webhook)
│   ├── handlers/               # start, traffic, payment, orders, statistics,
│   │                           # help, admin, common (FSM helpers)
│   ├── keyboards/              # main, platforms, services, orders, payments,
│   │                           # statistics, constants (callback schema)
│   ├── services/               # pricing, orders, payments, traffic_provider
│   ├── database/               # models.py, repository.py
│   └── states/order.py         # FSM state enum
├── tests/                      # smoke tests (no secrets, in-memory DB)
├── requirements.txt
├── .env.example
└── README.md
```

---

## Quick start

```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
# 1. put your BotFather token into BOT_TOKEN
# 2. put your numeric IDs into ADMIN_IDS (comma separated)
```

Run in development (polling):

```bash
python -m bot.main
```

Run the smoke tests:

```bash
$env:PYTHONPATH='.'
python tests\test_logic.py
python tests\test_webhook.py
```

> The bot needs a `BOT_TOKEN` from [@BotFather](https://t.me/BotFather).
> If it is unset, the bot exits with a clear message.

> **Python 3.13 note:** the bot uses python-telegram-bot's *async* lifecycle
> (`Application.initialize/start/stop/shutdown` + the updater) instead of the
> blocking `run_polling()`/`run_webhook()` wrappers. On Python 3.13+ the latter
> rely on the removed implicit `asyncio.get_event_loop()` behaviour, so we avoid
> them entirely. See `bot/main.py` for details.

---

## Environment variables

| Variable             | Default      | Description                                   |
| -------------------- | ------------ | --------------------------------------------- |
| `BOT_TOKEN`          | *(required)* | Bot token from BotFather.                     |
| `ADMIN_IDS`          | `[]`         | Comma-separated numeric IDs with admin rights.|
| `DATABASE_URL`       | SQLite file  | e.g. `postgresql+asyncpg://…` for production. |
| `USE_WEBHOOK`        | `false`      | Set `true` to run as webhook instead of polling.|
| `WEBHOOK_URL`        | —            | Public HTTPS base URL.                        |
| `WEBHOOK_PATH`       | `/webhook`   | Path for the webhook.                         |
| `WEB_PORT`           | `8443`       | Port for the webhook server.                  |
| `CRYPTO_WALLET_ADDRESS` | —         | Wallet address shown to crypto buyers.        |
| `MIN_QUANTITY`       | `100`        | Global minimum order quantity.                |
| `MAX_QUANTITY`       | `1000000`    | Global maximum order quantity.                |

Keep **all** secrets in environment variables. Never commit `.env`.

### Crypto payment details (section 21 / crypto flow)

After choosing `💳 Оплатить` → `₿ Оплатить криптой` the bot asks the user to
pick one of four currencies:

- `🔴 TRON (TRX)` — verified via **TronGrid** (`api.trongrid.io`)
- `⚪ LITECOIN (LTC)` — **Blockchair** (public address explorer link; the public
  API for address-transactions is restricted, so the adapter falls back to a
  manual-explorer URL supplied by `LITECOIN_EXPLORER_URL`)
- `💎 TON (GRAM)` — verified via **TON Center** (`toncenter.com/api/v2`)
- `🟣 SOLANA (SOL)` — verified via **Helius** JSON-RPC (`SOLANA_RPC_URL`)

A live RUB exchange rate is fetched from CoinGecko at invoice creation and then
**frozen on the order** (never auto-re-priced). The crypto amount is computed in
`Decimal` (e.g. `500 ₽ / 91.54 = 5.4621 TON`).

When the user presses `✅ Оплатил` the bot performs a **server-side blockchain
check** (never trusts a user-supplied hash / amount / price):

1. resolve order → 2. ownership → 3. pending → 4. not expired →
5. query the network indexer → 6. currency → 7. exact `Decimal` amount →
8. timestamp sanity → 9. duplicate-tx-hash guard → 10. confirmations →
11. only then `payment_status = paid` + `transaction_hash` stored.

`❌ Отменить` cancels a pending order. Underpayments are reported but never
marked as paid. The `✅ Оплатил` / `🔄 Проверить снова` buttons are
rate-limited (10 s) to protect the blockchain APIs.

Required secrets (see `.env.example`): `TRONGRID_API_KEY`, `TONCENTER_API_KEY`,
`SOLANA_RPC_URL`. Optional: `CRYPTO_INVOICE_TTL_SECONDS`.

---

## Payment architecture (section 21)

`bot/services/payments.py` defines `PaymentProvider` and two implementations:

- **`CryptoPaymentProvider`** — an integration point for a crypto gateway. In
  staging it creates a pending `Payment` and returns a wallet address + payment
  reference. Wire the gateway's confirmed-transaction webhook to
  `webhook_confirm_order` (idempotent).
- **`TelegramStarsProvider`** — uses Telegram Native Payments. The order is
  confirmed through the `pre_checkout`/`successful_payment` update path, again
  through the same idempotent confirm function.

Prices for confirmation are **always re-read from the DB** (`order.total_price`),
never from an incoming webhook payload.

### Idempotency guarantees
- Re-confirming the same payment → no-op.
- Confirming an order already paid via a *different* payment → the new payment
  is rejected (`DuplicatePaymentError`) and the order keeps its original paid
  state.
- Duplicate webhook delivery → safely ignored.

---

## Security notes (section 23)

- All `callback_data` is parsed and validated; IDs are re-checked against the DB.
- Order price and total are recomputed server-side on every confirmation.
- Quantity is validated (min/max from config / per-service) as a positive int.
- URLs must match `http(s)://…` and are length-capped.
- Admin actions verify membership in `ADMIN_IDS`.
- User-facing errors never expose stack traces (see the global error handler in
  `bot/main.py`).

---

## Extending for real providers

- **Traffic fulfilment:** implement `bot/services/traffic_provider.py`
  `TrafficProvider.submit_order` against the SMM provider REST API and swap the
  stub in `get_traffic_provider`.
- **Crypto gateway:** implement signature verification inside
  `CryptoPaymentProvider.handle_webhook` before calling `webhook_confirm_order`.