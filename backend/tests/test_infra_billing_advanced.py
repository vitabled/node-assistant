"""
Tests for advanced billing features (Wave-10 Extension).
"""
import pytest
from datetime import datetime, timedelta, timezone

from app.services.infra_billing_advanced import (
    CostForecast,
    BudgetManager,
    AlertManager,
    OptimizationEngine,
)


# ─────────────────────────────────────────────────────────────
# CostForecast tests
# ─────────────────────────────────────────────────────────────

def test_cost_forecast_empty_payments():
    """Должно обработать пустую историю платежей."""
    forecast = CostForecast([], [])
    assert forecast.monthly_burn_rate() == 0.0
    assert forecast.project_spend(30) == 0.0


def test_cost_forecast_burn_rate():
    """Вычислить средний monthly burn rate."""
    payments = [
        {"ts": int(datetime.now(tz=timezone.utc).timestamp()), "amount": 1000, "type": "charge"},
        {"ts": int((datetime.now(tz=timezone.utc) - timedelta(days=30)).timestamp()), "amount": 900, "type": "charge"},
        {"ts": int((datetime.now(tz=timezone.utc) - timedelta(days=60)).timestamp()), "amount": 1100, "type": "charge"},
    ]
    forecast = CostForecast(payments, [])
    rate = forecast.monthly_burn_rate()
    assert 900 <= rate <= 1100


def test_cost_forecast_project_fixed():
    """Вычислить projection для fixed услуг."""
    services = [
        {"cost": 100, "billing_type": "fixed", "created_at": int(datetime.now(tz=timezone.utc).timestamp())},
        {"cost": 200, "billing_type": "fixed", "created_at": int(datetime.now(tz=timezone.utc).timestamp())},
    ]
    forecast = CostForecast([], services)
    monthly = forecast.project_spend(30)
    assert monthly == 300.0  # 100 + 200


def test_cost_forecast_project_hourly():
    """Вычислить projection для hourly услуг (в месяц)."""
    services = [
        {"cost": 10, "billing_type": "hourly", "created_at": int(datetime.now(tz=timezone.utc).timestamp())},
    ]
    forecast = CostForecast([], services)
    monthly = forecast.project_spend(30)
    # 10 * 24 * 30 = 7200
    assert monthly == 7200.0


def test_cost_forecast_trend_stable():
    """Обнаружить стабильный тренд."""
    now = datetime.now(tz=timezone.utc)
    payments = []
    for i in range(10):
        payments.append({
            "ts": int((now - timedelta(days=i)).timestamp()),
            "amount": 1000,
            "type": "charge",
        })
    forecast = CostForecast(payments, [])
    trend = forecast.trend(days=90)
    assert trend["trend"] == "stable"
    assert abs(trend["coefficient"]) < 0.1


def test_cost_forecast_trend_increasing():
    """Обнаружить растущий тренд."""
    now = datetime.now(tz=timezone.utc)
    payments = []
    # Первая половина: низкие платежи
    for i in range(5, 10):
        payments.append({"ts": int((now - timedelta(days=i)).timestamp()), "amount": 500, "type": "charge"})
    # Вторая половина: высокие платежи
    for i in range(0, 5):
        payments.append({"ts": int((now - timedelta(days=i)).timestamp()), "amount": 1500, "type": "charge"})
    forecast = CostForecast(payments, [])
    trend = forecast.trend(days=90)
    assert trend["trend"] == "increasing"
    assert trend["coefficient"] > 0.1


def test_cost_forecast_anomalies():
    """Обнаружить аномалии в платежах (outliers)."""
    payments = [
        {"amount": 100, "type": "charge"},
        {"amount": 105, "type": "charge"},
        {"amount": 95, "type": "charge"},
        {"amount": 102, "type": "charge"},
        {"amount": 500, "type": "charge"},  # outlier
    ]
    forecast = CostForecast(payments, [])
    anomalies = forecast.anomalies(sensitivity=2.0)
    assert len(anomalies) > 0
    # Последний платёж должен быть flagged
    assert any(a["amount"] == 500 for a in anomalies)


# ─────────────────────────────────────────────────────────────
# BudgetManager tests
# ─────────────────────────────────────────────────────────────

def test_budget_manager_no_budget():
    """Должно обработать отсутствие бюджета."""
    manager = BudgetManager({})
    status = manager.check_budget("project1", 1000)
    assert status["status"] == "no_budget"


def test_budget_manager_ok():
    """Статус OK когда расходы ниже порога."""
    budgets = {"project1": {"limit": 5000, "alert_at": 0.8}}
    manager = BudgetManager(budgets)
    status = manager.check_budget("project1", 2000)
    assert status["status"] == "ok"
    assert status["percent"] == 40.0


def test_budget_manager_alert():
    """Alert когда приближаемся к лимиту."""
    budgets = {"project1": {"limit": 5000, "alert_at": 0.8}}
    manager = BudgetManager(budgets)
    status = manager.check_budget("project1", 4500)
    assert status["status"] == "alert"
    assert status["percent"] == 90.0


def test_budget_manager_exceeded():
    """Exceeded когда расходы выше лимита."""
    budgets = {"project1": {"limit": 5000, "alert_at": 0.8}}
    manager = BudgetManager(budgets)
    status = manager.check_budget("project1", 6000)
    assert status["status"] == "exceeded"
    assert status["remaining"] == -1000


def test_budget_manager_suggestions():
    """Должно давать рекомендации."""
    manager = BudgetManager({})
    status_exceeded = {"status": "exceeded"}
    suggestions = manager.suggest_actions(status_exceeded)
    assert len(suggestions) > 0
    assert any("бюджет" in s.lower() for s in suggestions)


# ─────────────────────────────────────────────────────────────
# AlertManager tests
# ─────────────────────────────────────────────────────────────

def test_alert_manager_low_balance():
    """Обнаружить низкий баланс."""
    manager = AlertManager()
    providers = [
        {"name": "Provider A", "balance": 500, "low_balance_threshold": 1000},
        {"name": "Provider B", "balance": 2000, "low_balance_threshold": 1000},
    ]
    alerts = manager.check_low_balance(providers)
    assert len(alerts) == 1
    assert alerts[0]["provider"] == "Provider A"
    assert alerts[0]["type"] == "low_balance"


def test_alert_manager_cost_spikes():
    """Обнаружить скачки в расходах."""
    payments = [
        {"amount": 100, "type": "charge"},
        {"amount": 105, "type": "charge"},
        {"amount": 95, "type": "charge"},
        {"amount": 600, "type": "charge"},  # spike
    ]
    forecast = CostForecast(payments, [])
    manager = AlertManager()
    alerts = manager.check_cost_spikes(forecast)
    assert len(alerts) > 0


def test_alert_manager_inactive_services():
    """Обнаружить неиспользуемые услуги."""
    old_timestamp = int((datetime.now(tz=timezone.utc) - timedelta(days=60)).timestamp())
    services = [
        {
            "id": "svc1",
            "name": "Old Service",
            "kind": "vps",
            "created_at": old_timestamp,
            "next_billing_at": "",  # Нет платежей
        },
        {
            "id": "svc2",
            "name": "Active Service",
            "kind": "vps",
            "created_at": old_timestamp,
            "next_billing_at": "2099-12-31",
        },
    ]
    manager = AlertManager()
    alerts = manager.check_inactive_services(services, days_threshold=30)
    assert len(alerts) > 0
    assert any(a["id"] == "svc1" for a in alerts)


# ─────────────────────────────────────────────────────────────
# OptimizationEngine tests
# ─────────────────────────────────────────────────────────────

def test_optimization_service_mix_high_hourly():
    """Рекомендовать Reserved при высоком проценте hourly."""
    services = [
        {"cost": 10, "billing_type": "hourly"},  # 10 * 730 = 7300/mo
        {"cost": 100, "billing_type": "fixed"},   # 100/mo
    ]
    payments = []
    forecast = CostForecast(payments, services)
    engine = OptimizationEngine(forecast, services)
    recommendations = engine.analyze_service_mix()
    assert len(recommendations) > 0
    assert any("reserved" in r.get("type", "").lower() for r in recommendations)


def test_optimization_provider_switching():
    """Рекомендовать переход между провайдерами."""
    services = []
    forecast = CostForecast([], services)
    engine = OptimizationEngine(forecast, services)
    provider_costs: dict[str, float] = {
        "provider_a": 1000.0,
        "provider_b": 1200.0,
        "provider_c": 4000.0,  # Outlier
    }
    recommendations = engine.suggest_provider_switching(provider_costs)
    # provider_c выше среднего на 30%
    assert any("provider_c" in r.get("from_provider", "") for r in recommendations)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
