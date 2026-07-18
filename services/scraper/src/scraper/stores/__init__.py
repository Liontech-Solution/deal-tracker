"""Scrapers por tienda (pluggable)."""

from .base import BaseStore, ListingEntry, ScrapedProduct, ScrapedVariant

__all__ = ["BaseStore", "ListingEntry", "ScrapedProduct", "ScrapedVariant"]
