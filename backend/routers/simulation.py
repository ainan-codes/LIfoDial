"""
backend/routers/simulation.py — Simulation Testing endpoints.
POST /agents/{id}/simulate → run single scenario
POST /agents/{id}/simulate/all → run all 8 scenarios
GET  /agents/{id}/simulation/scenarios → list available scenarios
"""
import logging
from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel
from sqlalchemy import select
from typing import Optional

from backend.auth import CurrentUser
from backend.db import async_session
from backend.models.agent_config import AgentConfig
from backend.services.simulation import (
    run_simulation,
    run_all_simulations,
    list_scenarios,
    SIMULATION_SCENARIOS,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class SimulateRequest(BaseModel):
    scenario: str
    # e.g. "hindi_booking", "emergency_detection"


# ── GET /agents/{agent_id}/simulation/scenarios ───────────────────────────────

@router.get("/agents/{agent_id}/simulation/scenarios")
async def get_scenarios(agent_id: str, user: CurrentUser = None) -> list[dict]:
    """Return list of available simulation scenarios with metadata.
    Static catalogue, not agent-specific data — login required, no ownership check."""
    return list_scenarios()


# ── POST /agents/{agent_id}/simulate ─────────────────────────────────────────

@router.post("/agents/{agent_id}/simulate")
async def simulate_single(agent_id: str, body: SimulateRequest, user: CurrentUser = None) -> dict:
    """
    Run a single simulation scenario for an agent.
    Returns full conversation + scores.
    ETA: 5-10 seconds per scenario.
    """
    if body.scenario not in SIMULATION_SCENARIOS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scenario '{body.scenario}'. "
                   f"Available: {list(SIMULATION_SCENARIOS.keys())}",
        )
    try:
        async with async_session() as db:
            agent_result = await db.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )
            agent = agent_result.scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            user.require_owns(str(agent.tenant_id))

            result = await run_simulation(agent_id, body.scenario, db)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Simulation error for agent %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /agents/{agent_id}/simulate/all ─────────────────────────────────────

@router.post("/agents/{agent_id}/simulate/all")
async def simulate_all(agent_id: str, user: CurrentUser = None) -> dict:
    """
    Run all 8 predefined scenarios for an agent and return aggregate results.
    ETA: 30-60 seconds. Runs scenarios sequentially to respect rate limits.
    """
    try:
        async with async_session() as db:
            agent_result = await db.execute(
                select(AgentConfig).where(AgentConfig.id == agent_id)
            )
            agent = agent_result.scalar_one_or_none()
            if not agent:
                raise HTTPException(status_code=404, detail="Agent not found")
            user.require_owns(str(agent.tenant_id))

            result = await run_all_simulations(agent_id, db)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Full simulation error for agent %s: %s", agent_id, e)
        raise HTTPException(status_code=500, detail=str(e))
