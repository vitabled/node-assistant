"""
Advanced billing features for Infra-billing subsystem (Wave-10 Extension).

Новые функции, вдохновлённые vitabled/mirror-billing-monitor:
- Cost forecasting (trend analysis + projection)
- Budget caps per project/provider (enforcement)
- Usage alerts + anomaly detection
- Cost optimization suggestions
- Multi-currency support improvements

Все функции работают с существующим infra_billing_store, добавляя
верхний слой аналитики и управления.
"""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.services import infra_billing_store as store

log = logging.getLogger("infra_billing_advanced")


# ═══════════════════════════════════════════════════════════════
# 1. Cost forecasting & trend analysis
# ═══════════════════════════════════════════════════════════════

class CostForecast:
    """Прогноз затрат на основе исторических платежей и текущей конфигурации."""

    def __init__(self, payment_history: list[dict], service_costs: list[dict]):
        """
        :param payment_history: список платежей (ts, amount, currency, type)
        :param service_costs: список услуг (cost, billing_type, created_at)
        """
        self.payments = payment_history
        self.services = service_costs

    def monthly_burn_rate(self) -> float:
        """Среднемесячная трата на основе истории. Возврат в базовой валюте."""
        if not self.payments:
            return 0.0
        charges = [
            p for p in self.payments
            if p.get("type") in ("charge", "adjustment")
        ]
        if not charges:
            return 0.0
        amounts = [p.get("amount", 0) for p in charges]
        return statistics.mean(amounts) if amounts else 0.0

    def project_spend(self, days: int = 30) -> float:
        """Прогноз затрат на N дней вперёд на основе текущей конфигурации."""
        total = 0.0
        for svc in self.services:
            if svc.get("billing_type") == "hourly":
                cost = svc.get("cost", 0)
                total += cost * 24 * days
            else:  # fixed monthly
                cost = svc.get("cost", 0)
                total += cost * (days / 30.0)
        return round(total, 2)

    def trend(self, days: int = 90) -> dict[str, Any]:
        """Тренд затрат за N дней: возрастает/убывает/стабильно + коэффициент."""
        now = datetime.now(tz=timezone.utc)
        cutoff = int((now - timedelta(days=days)).timestamp())

        recent = [
            p for p in self.payments
            if p.get("ts", 0) >= cutoff and p.get("type") == "charge"
        ]
        if len(recent) < 2:
            return {"trend": "insufficient_data", "coefficient": 0.0}

        amounts = [p.get("amount", 0) for p in recent]
        first_half = statistics.mean(amounts[: len(amounts) // 2])
        second_half = statistics.mean(amounts[len(amounts) // 2 :])

        if first_half == 0:
            return {"trend": "insufficient_data", "coefficient": 0.0}

        coefficient = (second_half - first_half) / first_half
        if coefficient > 0.1:
            trend = "increasing"
        elif coefficient < -0.1:
            trend = "decreasing"
        else:
            trend = "stable"

        return {"trend": trend, "coefficient": round(coefficient, 3)}

    def anomalies(self, sensitivity: float = 2.0) -> list[dict]:
        """Обнаружение аномалий в платежах (outliers by std dev)."""
        charges = [
            p for p in self.payments
            if p.get("type") in ("charge", "adjustment")
        ]
        if len(charges) < 3:
            return []

        amounts = [p.get("amount", 0) for p in charges]
        mean = statistics.mean(amounts)
        stdev = statistics.stdev(amounts) if len(amounts) > 1 else 0

        if stdev == 0:
            return []

        anomalies = []
        for p in charges:
            amount = p.get("amount", 0)
            z_score = abs((amount - mean) / stdev)
            if z_score > sensitivity:
                anomalies.append({
                    "ts": p.get("ts", 0),
                    "amount": amount,
                    "z_score": round(z_score, 2),
                    "deviation": round((amount - mean) / mean * 100, 1) if mean else 0,
                })
        return anomalies


# ═══════════════════════════════════════════════════════════════
# 2. Budget caps & enforcement
# ═══════════════════════════════════════════════════════════════

class BudgetManager:
    """Управление бюджетами и контроль перерасходов по проектам/провайдерам."""

    # На практике бюджеты хранятся в отдельной таблице; здесь учим их как dict.
    def __init__(self, budgets: Optional[dict[str, dict]] = None):
        """
        :param budgets: {entity_id: {"limit": 10000, "period": "monthly", "alert_at": 0.8}}
        """
        self.budgets = budgets or {}

    def check_budget(self, entity_id: str, current_spend: float) -> dict[str, Any]:
        """Проверить статус бюджета: в норме, у предупреждения, или превышен."""
        if entity_id not in self.budgets:
            return {"status": "no_budget", "message": "Бюджет не установлен"}

        budget = self.budgets[entity_id]
        limit = budget.get("limit", 0)
        alert_threshold = budget.get("alert_at", 0.8)

        if limit <= 0:
            return {"status": "no_limit", "message": "Бюджет на 0 или не задан"}

        percent = current_spend / limit
        remaining = limit - current_spend

        if current_spend > limit:
            return {
                "status": "exceeded",
                "limit": limit,
                "spent": current_spend,
                "remaining": remaining,
                "percent": round(percent * 100, 1),
                "message": f"Превышен бюджет на {abs(remaining):.2f}",
            }
        elif percent >= alert_threshold:
            return {
                "status": "alert",
                "limit": limit,
                "spent": current_spend,
                "remaining": remaining,
                "percent": round(percent * 100, 1),
                "message": f"Израсходовано {percent * 100:.0f}% бюджета",
            }
        else:
            return {
                "status": "ok",
                "limit": limit,
                "spent": current_spend,
                "remaining": remaining,
                "percent": round(percent * 100, 1),
            }

    def suggest_actions(self, status: dict) -> list[str]:
        """Рекомендации на основе статуса бюджета."""
        suggestions = []
        status_key = status.get("status", "")

        if status_key == "exceeded":
            suggestions.append("🚨 Увеличьте бюджет или снизьте расходы")
            suggestions.append("Проверьте неиспользуемые ресурсы на удаление")
            suggestions.append("Рассмотрите Reserved Instances (у облаков) для снижения цены")

        elif status_key == "alert":
            percent = status.get("percent", 0)
            if percent > 90:
                suggestions.append("⚠️  Бюджет почти исчерпан")
            else:
                suggestions.append("📊 Начните мониторить рост расходов")
            suggestions.append("Включите cost tagging для лучшей видимости")

        return suggestions


# ═══════════════════════════════════════════════════════════════
# 3. Usage & cost alerts
# ═══════════════════════════════════════════════════════════════

class AlertManager:
    """Система алертов о расходах, аномалиях и других событиях."""

    ALERT_TYPES = {"low_balance", "budget_exceeded", "cost_spike", "service_inactive"}

    def __init__(self, account_id: Optional[str] = None):
        self.account_id = account_id
        self.alerts: list[dict] = []

    def check_low_balance(
        self, providers: list[dict], threshold_percent: float = 0.2
    ) -> list[dict]:
        """Проверить баланс на всех провайдерах."""
        alerts = []
        for p in providers:
            balance = p.get("balance", 0)
            threshold = p.get("low_balance_threshold", 0)

            if threshold > 0 and balance <= threshold:
                alerts.append({
                    "type": "low_balance",
                    "provider": p.get("name", "Unknown"),
                    "balance": balance,
                    "threshold": threshold,
                    "severity": "critical" if balance < threshold * 0.5 else "warning",
                })
        return alerts

    def check_cost_spikes(
        self, forecast: CostForecast, spike_threshold: float = 1.5
    ) -> list[dict]:
        """Обнаружить резкие скачки в расходах."""
        rate = forecast.monthly_burn_rate()
        trend = forecast.trend(days=30)
        anomalies = forecast.anomalies(sensitivity=2.0)

        alerts = []
        if anomalies:
            for anom in anomalies:
                alerts.append({
                    "type": "cost_spike",
                    "amount": anom["amount"],
                    "z_score": anom["z_score"],
                    "deviation_percent": anom["deviation"],
                    "ts": anom["ts"],
                })

        # Проверить коэффициент тренда
        coeff = trend.get("coefficient", 0)
        if coeff > 0.3:  # 30% скачок за месяц
            alerts.append({
                "type": "rapid_growth",
                "coefficient": coeff,
                "message": f"Расходы растут на {coeff * 100:.0f}% в месяц",
            })

        return alerts

    def check_inactive_services(
        self, services: list[dict], days_threshold: int = 30
    ) -> list[dict]:
        """Найти услуги без платежей за последний N дней."""
        now = int(datetime.now(tz=timezone.utc).timestamp())
        cutoff = now - (days_threshold * 86400)

        alerts = []
        for svc in services:
            created_at = svc.get("created_at", now)
            if created_at < cutoff:
                # Была создана > N дней назад, но нет платежей?
                # На практике нужна проверка платежей в истории.
                if not svc.get("next_billing_at"):
                    alerts.append({
                        "type": "service_inactive",
                        "service": svc.get("name", "Unknown"),
                        "id": svc.get("id", ""),
                        "kind": svc.get("kind", ""),
                    })

        return alerts


# ═══════════════════════════════════════════════════════════════
# 4. Cost optimization engine
# ═══════════════════════════════════════════════════════════════

class OptimizationEngine:
    """Рекомендации по оптимизации расходов."""

    def __init__(self, forecast: CostForecast, services: list[dict]):
        self.forecast = forecast
        self.services = services

    def analyze_service_mix(self) -> list[dict]:
        """Анализ микса услуг (может ли гибридная модель дешевле)."""
        recommendations = []

        fixed_cost = sum(
            s.get("cost", 0)
            for s in self.services
            if s.get("billing_type") == "fixed"
        )
        hourly_cost = sum(
            s.get("cost", 0) * 730
            for s in self.services
            if s.get("billing_type") == "hourly"
        )
        total_monthly = fixed_cost + hourly_cost

        if total_monthly == 0:
            return recommendations

        hourly_percent = (hourly_cost / total_monthly) * 100

        if hourly_percent > 50:
            recommendations.append({
                "type": "consider_reserved",
                "message": "Высокий процент почасовых услуг. Рассмотрите Reserved Instances/Reserved Capacity для снижения цены на 20-40%",
                "potential_savings": total_monthly * 0.25,
            })
        elif hourly_percent < 10:
            recommendations.append({
                "type": "rightsizing",
                "message": "Большинство фиксированные услуги. Проверьте, не переплачиваете ли за мощность.",
            })

        return recommendations

    def suggest_provider_switching(
        self, provider_costs: dict[str, float]
    ) -> list[dict]:
        """Предложить переход между провайдерами для экономии."""
        recommendations = []

        if not provider_costs:
            return recommendations

        avg_cost = sum(provider_costs.values()) / len(provider_costs)
        for provider, cost in provider_costs.items():
            if cost > avg_cost * 1.3:
                recommendations.append({
                    "type": "provider_switch",
                    "from_provider": provider,
                    "current_cost": cost,
                    "market_avg": avg_cost,
                    "potential_savings": cost - avg_cost,
                })

        return recommendations

    def identify_unused_resources(self) -> list[dict]:
        """Найти потенциально неиспользуемые ресурсы."""
        recommendations = []

        for svc in self.services:
            # На практике здесь использовалась бы реальная метрика использования.
            # Сейчас это placeholder.
            if not svc.get("next_billing_at"):
                recommendations.append({
                    "type": "potential_unused",
                    "service": svc.get("name", ""),
                    "kind": svc.get("kind", ""),
                    "cost": svc.get("cost", 0),
                })

        return recommendations


# ═══════════════════════════════════════════════════════════════
# 5. Async public API
# ═══════════════════════════════════════════════════════════════

async def forecast_costs(account_id: Optional[str] = None) -> dict[str, Any]:
    """Получить полный прогноз затрат для аккаунта."""
    payments = await store.payments(account_id)
    services = await store.services(account_id)

    forecast = CostForecast(payments, services)

    return {
        "monthlyBurnRate": round(forecast.monthly_burn_rate(), 2),
        "projectedMonthly": forecast.project_spend(days=30),
        "projectedQuarterly": forecast.project_spend(days=90),
        "trend": forecast.trend(days=90),
        "anomalies": forecast.anomalies(),
    }


async def evaluate_budget(
    project_id: str, budgets: Optional[dict] = None, account_id: Optional[str] = None
) -> dict[str, Any]:
    """Оценить статус бюджета для проекта."""
    if budgets is None:
        budgets = {}

    services = await store.services(account_id)
    project_services = [
        s for s in services if s.get("project_id") == project_id
    ]

    current_spend = sum(
        s.get("cost", 0) * (730 if s.get("billing_type") == "hourly" else 1)
        for s in project_services
    )

    manager = BudgetManager(budgets)
    status = manager.check_budget(project_id, current_spend)
    status["suggestions"] = manager.suggest_actions(status)

    return status


async def check_alerts(account_id: Optional[str] = None) -> dict[str, Any]:
    """Проверить все типы алертов для аккаунта."""
    manager = AlertManager(account_id)

    providers = await store.provider_meta_all(account_id)
    services = await store.services(account_id)
    payments = await store.payments(account_id)

    provider_list = [
        {
            "name": k,
            "balance": v.get("balance", 0),
            "low_balance_threshold": v.get("low_balance_threshold", 0),
        }
        for k, v in providers.items()
    ]

    forecast = CostForecast(payments, services)
    all_alerts = []

    all_alerts.extend(manager.check_low_balance(provider_list))
    all_alerts.extend(manager.check_cost_spikes(forecast))
    all_alerts.extend(manager.check_inactive_services(services))

    return {
        "total": len(all_alerts),
        "alerts": all_alerts,
        "summary": {
            "critical": len([a for a in all_alerts if a.get("severity") == "critical"]),
            "warning": len([a for a in all_alerts if a.get("severity") == "warning"]),
        },
    }


async def get_optimization_recommendations(
    account_id: Optional[str] = None,
) -> dict[str, Any]:
    """Получить рекомендации по оптимизации затрат."""
    payments = await store.payments(account_id)
    services = await store.services(account_id)
    pmeta = await store.provider_meta_all(account_id)

    forecast = CostForecast(payments, services)
    engine = OptimizationEngine(forecast, services)

    provider_costs = {}
    for svc in services:
        provider = svc.get("provider_uuid", "unknown")
        cost = svc.get("cost", 0) * (730 if svc.get("billing_type") == "hourly" else 1)
        provider_costs[provider] = provider_costs.get(provider, 0) + cost

    recommendations = []
    recommendations.extend(engine.analyze_service_mix())
    recommendations.extend(engine.suggest_provider_switching(provider_costs))
    recommendations.extend(engine.identify_unused_resources())

    return {
        "total_recommendations": len(recommendations),
        "recommendations": recommendations,
        "estimated_savings": sum(
            r.get("potential_savings", 0) for r in recommendations
        ),
    }
