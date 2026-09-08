"""User-facing states for the Support Bot.

The Support Bot is a separate application but follows the same light-weight
state pattern as the Traffic Bot (context.user_data["sup_state"]). Values are
ours alone, so there is no FSM collision with the Traffic Bot.
"""
from __future__ import annotations

from enum import Enum, auto


class SupportState(Enum):
    main_menu = auto()
    creating = auto()          # user is describing a new ticket
    viewing = auto()           # placeholder; not strictly used
    # Admin reply mode; the ticket_id is stored in user_data["sup_admin_reply"].
    admin_reply = auto()