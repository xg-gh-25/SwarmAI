"""Middleware package for the Agent Platform API."""

from .error_handler import setup_error_handlers

__all__ = ["setup_error_handlers"]
