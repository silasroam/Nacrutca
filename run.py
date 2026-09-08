"""Convenience launcher: `python run.py`."""
import asyncio

from bot.main import main

if __name__ == "__main__":
    asyncio.run(main())