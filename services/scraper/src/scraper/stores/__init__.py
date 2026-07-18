"""Scrapers por tienda (pluggable)."""

from .base import BaseStore, ListingEntry, ScrapedProduct, ScrapedVariant, ScrapeScope

__all__ = [
    "BaseStore",
    "ListingEntry",
    "ScrapeScope",
    "ScrapedProduct",
    "ScrapedVariant",
]
