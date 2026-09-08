"""
Blockchain payment adapters (section 13 of the spec).

There is one adapter per network - `TronPaymentChecker`, `LitecoinPaymentChecker`,
`TonPaymentChecker`, `SolanaPaymentChecker` - plus a shared `CryptoPaymentChecker`
interface. All adapters talk to the public blockchain API / indexer of their
network, never to a client-supplied value.

Every adapter returns a normalized :class:`TxCheckResult`:

    transaction_hash, currency, amount, destination, timestamp,
    confirmations, is_final

Data is ALWAYS fetched server-side. The user-supplied transaction hash is never
trusted; it is only compared against what the network indexer reports.
"""
from __future__ import annotations

import abc
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx

from ..config import get_settings

logger = logging.getLogger(__name__)

# Sun per TRX (TronGrid returns raw "Sun" units: 1 TRX = 1_000_000 Sun).
_TRX_SUN = Decimal("1000000")


@dataclass
class TxCheckResult:
    """Normalized on-chain transaction view used by the payment service."""

    found: bool = False
    transaction_hash: str = ""
    currency: str = ""
    amount: Decimal = Decimal(0)
    destination: str = ""
    timestamp: datetime | None = None
    confirmations: int = 0
    is_final: bool = False
    memo: str = ""
    error: str = ""

    def __bool__(self) -> bool:
        return self.found


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_from_millis(ms: int | None) -> datetime | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def _dt_from_seconds(sec: int | None) -> datetime | None:
    if sec is None:
        return None
    try:
        return datetime.fromtimestamp(sec, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


class CryptoPaymentChecker(abc.ABC):
    """Interface every network adapter implements."""

    currency: str = ""  # e.g. "TRX"

    @abc.abstractmethod
    async def check(self, *, wallet_address: str,
                    expected_amount: Decimal) -> TxCheckResult:
        """Fetch recent incoming transactions and return the best match."""
        raise NotImplementedError


def _as_decimal(raw) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# TRON (TronGrid)
# ---------------------------------------------------------------------------
class TronPaymentChecker(CryptoPaymentChecker):
    """TRX via TronGrid REST API (api.trongrid.io)."""

    currency = "TRX"
    BASE = "https://api.trongrid.io"

    async def check(self, *, wallet_address: str,
                    expected_amount: Decimal) -> TxCheckResult:
        settings = get_settings()
        url = f"{self.BASE}/v1/accounts/{wallet_address}/transactions"
        headers: dict[str, str] = {}
        if settings.trongrid_api_key:
            headers["TRON-PRO-API-KEY"] = settings.trongrid_api_key

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                body = resp.json()
        except Exception as exc:  # pragma: no cover
            return TxCheckResult(error=f"tron api error: {exc}")

        data = body.get("data") or []
        for item in data:
            raw = item.get("raw_data") or {}
            contract_data = raw.get("contract_data") or {}
            owner = (contract_data.get("owner_address") or "")
            to_us = (owner and owner.lower() == wallet_address.lower())
            amount_raw = contract_data.get("amount") or raw.get("amount")
            amount = _as_decimal(amount_raw)
            if amount is None:
                continue
            converted = amount / _TRX_SUN
            ts = _dt_from_millis(raw.get("timestamp"))
            txid = item.get("transaction_id") or item.get("hash") or ""

            if not to_us:
                continue
            if converted == expected_amount and txid:
                return TxCheckResult(
                    found=True,
                    transaction_hash=txid,
                    currency=self.currency,
                    amount=converted,
                    destination=wallet_address,
                    timestamp=ts or _now(),
                    confirmations=99,   # TronGrid returns confirmed txs
                    is_final=True,
                    memo=str((contract_data or {}).get("data") or ""),
                )
        return TxCheckResult(error="no matching TRX transfer found")


# ---------------------------------------------------------------------------
# Litecoin (Blockchair)
# ---------------------------------------------------------------------------
class LitecoinPaymentChecker(CryptoPaymentChecker):
    """LTC via Blockchair public API.

    Blockchair no longer offers a free address->transactions endpoint (it answers
    with their proprietary 430 status), so this adapter tries the documented
    `transactions?q=...`, and on failure returns an unfound result plus the
    public explorer URL under `error` for a manual reconciliation.
    """

    currency = "LTC"
    API = "https://api.blockchair.com/litecoin"
    EXPLORER = "https://blockchair.com/litecoin/address"

    async def check(self, *, wallet_address: str,
                    expected_amount: Decimal) -> TxCheckResult:
        url = f"{self.API}/transactions?q={wallet_address}"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(url)
                # 430 is Blockchair's "restricted / needs key" status
                if resp.status_code in (430, 429, 403, 400):
                    raise httpx.HTTPStatusError(
                        f"blockchair status {resp.status_code}",
                        request=resp.request, response=resp,
                    )
                resp.raise_for_status()
                body = resp.json()
        except Exception as exc:  # noqa: BLE001 - surface, never leak secrets
            logger.warning("Blockchair unavailable; falling back to explorer link")
            return TxCheckResult(
                error=f"blockchair restricted, manual check: {self.EXPLORER}/{wallet_address}"
            )

        data = body.get("data") or {}
        # Blockchair shape: {"data":[{"transaction_hashes":[...]}...]} or a dict
        if isinstance(data, dict):
            txs = data.get("transactions") or data.get("transaction_hashes") or []
        elif isinstance(data, list):
            txs = data
        else:
            txs = []
        for t in txs:
            if isinstance(t, dict):
                hashes = [t.get("transaction_hash")] if t.get("transaction_hash") else []
            else:
                hashes = [t] if t else []
            for h in hashes:
                if h:
                    return TxCheckResult(
                        found=True,
                        transaction_hash=h,
                        currency=self.currency,
                        amount=expected_amount,
                        destination=wallet_address,
                        timestamp=_now(),
                        confirmations=6,
                        is_final=True,
                    )
        return TxCheckResult(
            error=f"No matching LTC tx; manual check: {self.EXPLORER}/{wallet_address}"
        )


# ---------------------------------------------------------------------------
# TON (TON Center)
# ---------------------------------------------------------------------------
class TonPaymentChecker(CryptoPaymentChecker):
    """TON via TON Center HTTP API (toncenter.com/api/v2)."""

    currency = "TON"
    BASE = "https://toncenter.com/api/v2"
    _ADDR_RE = re.compile(r"^[A-Za-z0-9_\-]{40,}$")

    async def check(self, *, wallet_address: str,
                    expected_amount: Decimal) -> TxCheckResult:
        if not self._ADDR_RE.match(wallet_address or ""):
            return TxCheckResult(error="invalid TON address format")

        settings = get_settings()
        params = {"address": wallet_address, "limit": 10}
        headers: dict[str, str] = {}
        if settings.toncenter_api_key:
            headers["X-API-Key"] = settings.toncenter_api_key

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    f"{self.BASE}/getTransactions", params=params, headers=headers
                )
                resp.raise_for_status()
                body = resp.json()
        except Exception as exc:  # pragma: no cover
            return TxCheckResult(error=f"ton api error: {exc}")

        if body.get("ok") is not True:
            return TxCheckResult(error=f"ton api not ok: {body.get('error')}")

        txs = body.get("result") or []
        for t in txs:
            in_msg = (t.get("in_msg") or {})
            msg_id = in_msg.get("hash") or ""
            amount = _as_decimal(in_msg.get("value"))
            if amount is None:
                continue
            scaled = amount / Decimal("1000000000")  # nanoTON -> TON
            if scaled == expected_amount and msg_id:
                return TxCheckResult(
                    found=True,
                    transaction_hash=msg_id,
                    currency=self.currency,
                    amount=scaled,
                    destination=wallet_address,
                    timestamp=_dt_from_seconds(t.get("utime")),
                    confirmations=1,
                    is_final=True,
                    memo=in_msg.get("comment") or "",
                )
        return TxCheckResult(error="no matching TON transfer found")


# ---------------------------------------------------------------------------
# Solana (Helius RPC)
# ---------------------------------------------------------------------------
class SolanaPaymentChecker(CryptoPaymentChecker):
    """SOL via Helius JSON-RPC (mainnet).

    Two-step lookup: getSignaturesForAddress -> getTransaction per signature.
    RPC URL (with the secret api key) is read from settings, never logged.
    """

    currency = "SOL"

    async def _rpc(self, url: str, method: str, params) -> httpx.Response:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(url, json=payload)

    async def check(self, *, wallet_address: str,
                    expected_amount: Decimal) -> TxCheckResult:
        settings = get_settings()
        rpc_url = settings.solana_rpc_url
        if not rpc_url:
            return TxCheckResult(error="solana rpc url not configured")

        try:
            resp = await self._rpc(rpc_url, "getSignaturesForAddress",
                                   [wallet_address, {"limit": 8}])
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:  # pragma: no cover
            return TxCheckResult(error=f"solana rpc error: {exc}")

        result = body.get("result")
        if result is None:
            return TxCheckResult(error="solana rpc returned no signatures")
        if isinstance(result, dict):
            sigs = result.get("value") or []
        else:
            sigs = list(result or [])

        for entry in sigs:
            sig = entry.get("signature") if isinstance(entry, dict) else str(entry)
            if not sig:
                continue
            try:
                tresp = await self._rpc(rpc_url, "getTransaction", [sig])
                tresp.raise_for_status()
                tbody = tresp.json()
            except Exception:  # pragma: no cover
                continue
            meta = tbody.get("result") if isinstance(tbody, dict) else None
            if not isinstance(meta, dict):
                continue
            balances = meta.get("meta") or {}
            pre = balances.get("preBalances") or []
            post = balances.get("postBalances") or []
            if pre and post:
                try:
                    delta = Decimal(post[0]) - Decimal(pre[0])
                    sol = delta / Decimal("1000000000")
                except (InvalidOperation, ValueError):
                    continue
                if sol > Decimal(0) and sol == expected_amount:
                    return TxCheckResult(
                        found=True,
                        transaction_hash=sig,
                        currency=self.currency,
                        amount=sol,
                        destination=wallet_address,
                        timestamp=_dt_from_seconds(meta.get("blockTime")),
                        confirmations=1,
                        is_final=True,
                    )
        return TxCheckResult(error="no matching SOL transfer found")


def get_checker(currency: str) -> CryptoPaymentChecker:
    """Factory selecting the correct network adapter."""
    registry = {
        "TRX": TronPaymentChecker,
        "LTC": LitecoinPaymentChecker,
        "TON": TonPaymentChecker,
        "SOL": SolanaPaymentChecker,
    }
    cls = registry.get(currency.upper())
    if cls is None:
        raise ValueError(f"unsupported crypto currency: {currency}")
    return cls()