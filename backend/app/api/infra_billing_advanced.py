"""
Advanced billing API routes (Wave-10 Extension).

Новые endpoints для прогнозирования, управления бюджетом и оптимизации.
Работают наряду с основным modulo infra_billing.py.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import infra_billing_advanced as advanced

router = APIRouter(prefix="/api/infra-billing/advanced")

log = logging.getLogger("infra_billing_routes_advanced")


# ── Request models ────────────────────────────────────────────
class BudgetCapBody(BaseModel):
    """Установить бюджет для проекта или провайдера."""
    entity_id: str = Field(..., min_length=1)
    limit: float = Field(..., ge=0)
    period: str = "monthly"  # monthly | quarterly | annual
    alert_at: float = Field(default=0.8, ge=0, le=1.0)  # % от лимита для алерта


# ═══════════════════════════════════════════════════════════════
# 1. Forecasting & Analytics
# ═══════════════════════════════════════════════════════════════

@router.get("/forecast/costs")
async def forecast_costs():
    """Получить полный прогноз затрат: месячный burn-rate, аномалии, тренд."""
    try:
        forecast = await advanced.forecast_costs()
        return forecast
    except Exception as exc:
        log.error("forecast_costs failed: %s", exc)
        raise HTTPException(500, f"Ошибка прогноза: {str(exc)[:100]}")


@router.get("/forecast/monthly-projection")
async def monthly_projection():
    """Простое значение: сколько денег потратим в следующем месяце."""
    try:
        data = await advanced.forecast_costs()
        return {
            "projected_monthly_cost": data.get("projectedMonthly", 0),
            "burn_rate_daily": data.get("monthlyBurnRate", 0) / 30,
            "days_remaining": None,  # будет заполнено, если есть общий баланс
        }
    except Exception as exc:
        raise HTTPException(500, f"Ошибка расчёта: {str(exc)[:100]}")


# ═══════════════════════════════════════════════════════════════
# 2. Budget management
# ═══════════════════════════════════════════════════════════════

# На практике это сохранялось бы в БД; здесь это in-memory demo.
_BUDGETS: dict[str, dict] = {}


@router.get("/budgets")
async def list_budgets():
    """Список всех установленных бюджетов."""
    return {
        "budgets": [
            {
                "entity_id": eid,
                "limit": b.get("limit"),
                "period": b.get("period"),
                "alert_at": b.get("alert_at"),
            }
            for eid, b in _BUDGETS.items()
        ]
    }


@router.post("/budgets", status_code=201)
async def set_budget(body: BudgetCapBody):
    """Установить бюджет для проекта/провайдера."""
    _BUDGETS[body.entity_id] = {
        "limit": body.limit,
        "period": body.period,
        "alert_at": body.alert_at,
    }
    return {"ok": True, "entity_id": body.entity_id}


@router.get("/budgets/{entity_id}/status")
async def check_budget_status(entity_id: str):
    """Проверить текущий статус бюджета для сущности."""
    try:
        status = await advanced.evaluate_budget(entity_id, _BUDGETS)
        return status
    except Exception as exc:
        raise HTTPException(500, f"Ошибка проверки: {str(exc)[:100]}")


@router.delete("/budgets/{entity_id}")
async def delete_budget(entity_id: str):
    """Удалить бюджет."""
    if entity_id in _BUDGETS:
        del _BUDGETS[entity_id]
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# 3. Alerts & Monitoring
# ═══════════════════════════════════════════════════════════════

@router.get("/alerts")
async def list_alerts():
    """Получить все активные алерты (низкий баланс, скачки, неиспользуемое)."""
    try:
        alerts = await advanced.check_alerts()
        return alerts
    except Exception as exc:
        log.error("check_alerts failed: %s", exc)
        raise HTTPException(500, f"Ошибка: {str(exc)[:100]}")


@router.get("/alerts/critical")
async def critical_alerts():
    """Только критические алерты (требуют немедленного внимания)."""
    try:
        alerts = await advanced.check_alerts()
        critical = [
            a for a in alerts.get("alerts", [])
            if a.get("severity") == "critical" or a.get("type") == "budget_exceeded"
        ]
        return {
            "count": len(critical),
            "alerts": critical,
            "requires_action": len(critical) > 0,
        }
    except Exception as exc:
        raise HTTPException(500, f"Ошибка: {str(exc)[:100]}")


# ═══════════════════════════════════════════════════════════════
# 4. Cost optimization
# ═══════════════════════════════════════════════════════════════

@router.get("/optimize/recommendations")
async def optimization_recommendations():
    """Получить рекомендации по оптимизации расходов."""
    try:
        recommendations = await advanced.get_optimization_recommendations()
        return recommendations
    except Exception as exc:
        log.error("optimization_recommendations failed: %s", exc)
        raise HTTPException(500, f"Ошибка: {str(exc)[:100]}")


@router.get("/optimize/summary")
async def optimization_summary():
    """Краткое резюме: есть ли потенциал для экономии?"""
    try:
        rec = await advanced.get_optimization_recommendations()
        total_recommendations = rec.get("total_recommendations", 0)
        estimated_savings = rec.get("estimated_savings", 0)

        return {
            "optimization_opportunity": estimated_savings > 0,
            "total_recommendations": total_recommendations,
            "estimated_monthly_savings": round(estimated_savings, 2),
            "savings_percentage": 0,  # вычислится из текущего бюджета
        }
    except Exception as exc:
        raise HTTPException(500, f"Ошибка: {str(exc)[:100]}")


# ═══════════════════════════════════════════════════════════════
# 5. Health check
# ═══════════════════════════════════════════════════════════════

@router.get("/health")
async def health():
    """Проверить, что расширения загружены и работают."""
    return {
        "status": "ok",
        "modules": [
            "forecast",
            "budget_management",
            "alert_system",
            "optimization_engine",
        ],
        "version": "1.0",
    }
