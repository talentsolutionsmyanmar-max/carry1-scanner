"""Quantrex v0 paper research kernel.

This package is deliberately isolated from the legacy CARRY-DAY playbooks.
It consumes public USD-M data and cannot submit exchange orders.
"""

from .config import QuantrexConfig
from .contracts import Book, Side

__all__ = ["Book", "QuantrexConfig", "Side"]
