"""Development-only API endpoints.

These endpoints are only registered when DEBUG=true.
They provide utilities for development and testing.
"""
import logging

from fastapi import APIRouter

from scripts.generate_mock_data import generate_mock_data

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/generate-mock-data")
async def generate_mock_data_endpoint():
    """Generate mock data for development.

    Generates ToDos, Tasks, PlanItems, Communications, Artifacts,
    and Reflections for SwarmWS and a TestWS workspace.

    Skips if mock data already exists (idempotent).
    Only available when DEBUG=true.

    Requirements: 12.1-12.6
    """
    result = await generate_mock_data()
    return result
