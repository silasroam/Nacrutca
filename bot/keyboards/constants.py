"""
Central definitions of inline-callback payloads.

Callback data is treated as untrusted input (section 23): handlers always
re-validate the parsed IDs against the DB and never blindly trust them for
authorization or price decisions.
"""
from __future__ import annotations

# Namespace prefixes
CB = "cb"
SEP = ":"


def cb_make(*parts) -> str:
    return SEP.join(str(p) for p in parts)


def cb_parse(data: str) -> list[str]:
    return list(data.split(SEP))


# --- main menu ---
CB_BUY = "buy"
CB_STATS = CB + ":stats"
CB_MY_ORDERS = CB + ":myorders"
CB_HELP = CB + ":help"
CB_BACK = CB + ":back"
CB_SUPPORT = CB + ":support"

# --- platforms ---
CB_PLATFORM = "platform"
CB_PLATFORM_BACK = "platform:back"

# --- services ---
CB_SERVICE = "service"

# --- quantity (not a button, but state) ---
# --- url ---
CB_CONFIRM = CB + ":confirm"      # service:<id>:confirm
CB_CANCEL = CB + ":cancel"

# --- payment ---
CB_PAY_CRYPTO = CB + ":pay:crypto"
CB_PAY_STARS = CB + ":pay:stars"

# --- stars payment flow ---
CB_STARS_PAY = CB + ":stars:pay"        # pay:<order_id> (send official invoice)
CB_STARS_CANCEL = CB + ":stars:cancel"  # cancel:<order_id>

# --- crypto currency selection & invoice actions ---
CB_CRYPTO_WALLET = CB + ":crypto:w"        # wallet:<code> (choose currency)
CB_CRYPTO_PAID = CB + ":crypto:paid"       # paid:<order_id> (I paid)
CB_CRYPTO_RECHECK = CB + ":crypto:recheck" # recheck:<order_id>
CB_CRYPTO_CANCEL = CB + ":crypto:cancel"   # cancel:<order_id>
CB_CRYPTO_BACK = CB + ":crypto:back"       # back to payment methods

# --- orders ---
CB_ORDER_DETAIL = CB + ":order"
CB_ORDER_BACK = CB + ":orders_back"

# --- stats periods ---
CB_STAT_PERIOD = CB + ":stat"

# --- admin ---
CB_ADMIN = CB + ":admin"
CB_ADMIN_ORDERS = CB + ":admin:orders"
CB_ADMIN_USERS = CB + ":admin:users"
CB_ADMIN_PAYMENTS = CB + ":admin:payments"
CB_ADMIN_PRICES = CB + ":admin:prices"
CB_ADMIN_TOGGLE = CB + ":admin:toggle"
CB_ADMIN_SETSTATUS = CB + ":admin:setstatus"
CB_ADMIN_STATS = CB + ":admin:stats"
CB_ADMIN_BACK = CB + ":admin:back"