# Infra-Billing Wave-10 Extension (Advanced Features & New Providers)

## Обзор

Расширение добавляет 4 основные функции к существующей системе инфра-биллинга в Node Assistant:

### 1. **Cost Forecasting & Trend Analysis** 📊
- Прогнозирование затрат на основе исторических платежей
- Анализ тренда (растет, падает, стабильно)
- Обнаружение аномалий (outliers в платежах)
- Projection на 30/90 дней вперёд

### 2. **Budget Management** 💰
- Установка лимитов бюджета на проекты/провайдеров
- Контроль перерасхода с уровнями alert (80%) и exceeded (100%)
- Рекомендации по действиям при превышении

### 3. **Usage Alerts & Monitoring** 🚨
- Low balance alerts на провайдерах
- Cost spike detection (резкие скачки затрат)
- Inactive services detection (услуги без платежей 30+ дней)
- Критические и обычные уровни алертов

### 4. **Cost Optimization Engine** 🔧
- Анализ микса услуг (fixed vs hourly)
- Рекомендации по Reserved Instances
- Suggestions по переходу между провайдерами
- Identification неиспользуемых ресурсов

### 5. **New Provider Adapters** 🌐
- **Vultr** — поддержка balance, services, payments
- **Linode** — поддержка balance, services, payments, order (создание серверов)

---

## API Endpoints

### Forecasting

```http
GET /api/infra-billing/advanced/forecast/costs
```

**Response:**
```json
{
  "monthlyBurnRate": 5000.50,
  "projectedMonthly": 5000.50,
  "projectedQuarterly": 15001.50,
  "trend": {
    "trend": "stable",
    "coefficient": 0.02
  },
  "anomalies": [
    {
      "ts": 1692907200,
      "amount": 10000,
      "z_score": 3.45,
      "deviation": 150.5
    }
  ]
}
```

### Budget Management

```http
POST /api/infra-billing/advanced/budgets
Content-Type: application/json

{
  "entity_id": "project-123",
  "limit": 10000,
  "period": "monthly",
  "alert_at": 0.8
}
```

```http
GET /api/infra-billing/advanced/budgets/{entity_id}/status
```

**Response:**
```json
{
  "status": "alert",
  "limit": 10000,
  "spent": 8500,
  "remaining": 1500,
  "percent": 85.0,
  "suggestions": [
    "📊 Начните мониторить рост расходов"
  ]
}
```

### Alerts

```http
GET /api/infra-billing/advanced/alerts
```

**Response:**
```json
{
  "total": 3,
  "summary": {
    "critical": 1,
    "warning": 2
  },
  "alerts": [
    {
      "type": "low_balance",
      "provider": "Vultr",
      "balance": 50,
      "threshold": 100,
      "severity": "critical"
    }
  ]
}
```

### Optimization

```http
GET /api/infra-billing/advanced/optimize/recommendations
```

**Response:**
```json
{
  "total_recommendations": 2,
  "estimated_savings": 1500.0,
  "recommendations": [
    {
      "type": "consider_reserved",
      "message": "Высокий процент почасовых услуг...",
      "potential_savings": 1500.0
    }
  ]
}
```

---

## Использование в коде

### Cost Forecasting

```python
from app.services.infra_billing_advanced import CostForecast

# Получить платежи и услуги из хранилища
payments = await store.payments(account_id)
services = await store.services(account_id)

# Создать forecast
forecast = CostForecast(payments, services)

# Анализировать
burn_rate = forecast.monthly_burn_rate()  # Avg затраты в месяц
projection = forecast.project_spend(90)   # Projection на 90 дней
trend = forecast.trend()                   # Trend анализ
anomalies = forecast.anomalies()          # Outliers обнаружение
```

### Budget Management

```python
from app.services.infra_billing_advanced import BudgetManager

budgets = {
    "project-1": {"limit": 5000, "period": "monthly", "alert_at": 0.8},
    "project-2": {"limit": 10000, "period": "monthly", "alert_at": 0.85},
}

manager = BudgetManager(budgets)

# Проверить статус
status = manager.check_budget("project-1", current_spend=4200)
# status = {"status": "alert", "limit": 5000, "spent": 4200, "percent": 84.0, ...}

# Получить рекомендации
suggestions = manager.suggest_actions(status)
# suggestions = ["Бюджет почти исчерпан", ...]
```

### Alert Management

```python
from app.services.infra_billing_advanced import AlertManager

manager = AlertManager(account_id=user_account)

# Проверить все типы алертов
alerts = manager.check_low_balance(providers)
alerts += manager.check_cost_spikes(forecast, spike_threshold=1.5)
alerts += manager.check_inactive_services(services, days_threshold=30)
```

### Optimization

```python
from app.services.infra_billing_advanced import OptimizationEngine

engine = OptimizationEngine(forecast, services)

# Анализировать микс услуг
mix_recs = engine.analyze_service_mix()

# Рекомендации по провайдерам
provider_costs = {"vultr": 1000, "linode": 1500, "aws": 4000}
prov_recs = engine.suggest_provider_switching(provider_costs)

# Неиспользуемые ресурсы
unused = engine.identify_unused_resources()
```

---

## Новые Адаптеры Провайдеров

### Vultr

**KIND:** `vultr`  
**TITLE:** Vultr  
**CAPS:** `balance`, `services`, `payments`  
**Required fields:**
- `token` (API token from https://www.vultr.com/api/)

**Особенности:**
- Все цены в USD
- Rate limit: 40 req/min
- Balance from `/v2/account`

### Linode

**KIND:** `linode`  
**TITLE:** Linode  
**CAPS:** `balance`, `services`, `payments`, `order`  
**Required fields:**
- `token` (API token from https://www.linode.com/api/)

**Особенности:**
- Все цены в USD
- Rate limit: 240 req/min (4 req/sec)
- Поддерживает заказ серверов через API
- Fixed plans без конструктора

---

## Интеграция с основным модулем

Все функции работают с существующим `infra_billing_store`:

```python
# Store functions used:
await store.payments(account_id)          # Получить платежи
await store.services(account_id)          # Получить услуги
await store.provider_meta_all(account_id) # Получить метаданные провайдеров
```

На практике новый слой (`infra_billing_advanced`) **не модифицирует** существующие таблицы —
это чистая аналитика поверх них.

---

## Тестирование

Unit-тесты находятся в `backend/tests/test_infra_billing_advanced.py`:

```bash
cd /opt/agent-cli/node-installer
python3 -m pytest backend/tests/test_infra_billing_advanced.py -v
```

Тесты включают:
- ✓ Cost forecast calculations
- ✓ Trend detection
- ✓ Anomaly detection
- ✓ Budget management
- ✓ Alert triggering
- ✓ Optimization recommendations

---

## Внедрение в Frontend

### Forecast Dashboard
```javascript
// Получить полный forecast
const forecast = await fetch('/api/infra-billing/advanced/forecast/costs').then(r => r.json());
// Показать на графике:
// - Monthly burn rate
// - Trend (стрелка вверх/вниз)
// - Projection timeline
// - Anomalies (красные точки)
```

### Budget Widget
```javascript
// Проверить бюджет для каждого проекта
const status = await fetch(`/api/infra-billing/advanced/budgets/${projectId}/status`).then(r => r.json());
// Покрасить в красный если exceeded, жёлтый если alert
```

### Alert Center
```javascript
// Получить критические алерты
const alerts = await fetch('/api/infra-billing/advanced/alerts/critical').then(r => r.json());
// Показать всплывающее уведомление если есть критические
```

---

## Performance Notes

### Async I/O
Все операции используют `asyncio.to_thread` для SQLite:
- Без блокировки основного event loop
- Поддерживает параллельные запросы

### Caching (Future)
При необходимости добавить Redis-кеш для:
- Forecast results (TTL: 1 hour)
- Recommendations (TTL: 6 hours)

### Database
현존하는 таблицы используются как-есть; бюджеты (на практике)
хранились бы в отдельной таблице с миграцией.

---

## Известные ограничения

1. **Бюджеты** — сейчас in-memory demo; на продакшене нужна БД таблица
2. **Linode ordering** — требует успешной авторизации и активного API токена
3. **Anomaly detection** — нужно минимум 3 платежа для стат анализа
4. **Trend analysis** — требует данные за N дней; меньше данных = менее точный тренд

---

## Backlog (Wave-11)

- [ ] Persistent budget storage in DB
- [ ] Scheduled cost reports (weekly email)
- [ ] Integration with third-party cost APIs
- [ ] Custom alert rules (user-defined thresholds)
- [ ] Reserved instance recommendations (per-provider)
- [ ] Cost anomaly ML model
- [ ] Export analytics to CSV/PDF
