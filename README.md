# Garmin Heart Rate Dashboard

Лёгковесный дашборд для мониторинга пульса (Heart Rate) из Garmin Connect. Берёт данные из неофициального Garmin Connect API, складывает в SQLite БД и показывает в браузере с автообновлением. Поддерживает несколько Garmin-аккаунтов с независимыми логинами.

```
Garmin Connect ──▶ worker (Python + Node helper) ──▶ SQLite ──▶ FastAPI ──▶ Frontend
```

---

## Структура проекта

```
GARMIN_BB_DASH/
├── backend/                 # FastAPI + SQLAlchemy 2.0 + Pydantic V2
│   ├── app/
│   │   ├── main.py          # Точка входа, CORS, роутер
│   │   ├── database.py      # SQLite engine, абсолютный путь
│   │   ├── models.py        # SQLAlchemy: BodyBatteryLog
│   │   ├── schemas.py       # Pydantic V2 schemas
│   │   └── api.py           # Все API эндпоинты
│   ├── requirements.txt
│   ├── Dockerfile
│   └── data/                # credentials.json (gitignored)
├── worker/                  # Python scheduler (APScheduler)
│   ├── worker.py            # Основная логика: scheduler + run_job
│   ├── garmin_client.py     # NodeGarminClient (subprocess wrapper)
│   ├── login_interactive.py # Интерактивный логин
│   ├── requirements.txt
│   └── Dockerfile
├── worker_node/             # Node.js helper для обхода Cloudflare
│   ├── fetch_heart_rate.js  # garmin-connect клиент, кэш токенов
│   ├── package.json
│   └── tokens/              # OAuth-токены (gitignored)
├── frontend/                # Vanilla JS + Chart.js
│   ├── index.html
│   ├── app.js               # Логика UI
│   └── styles.css
├── tests/                   # pytest suite (56 тестов)
├── data/                    # SQLite body_battery.db (gitignored)
└── docker-compose.yml
```

---

## Как это работает

### 1. Worker (`worker/worker.py`)

`BlockingScheduler` запускает `run_job` сразу при старте (если есть сохранённые credentials), потом повторяет каждые `POLL_MINUTES` минут (по умолчанию 5).

В `run_job`:
1. Загружаются credentials из `backend/data/credentials.json` (сохраняются при логине через `/api/v1/login`).
2. Создаётся `NodeGarminClient` и вызывается `client.login()` (логика дефержена в Node helper).
3. Из БД достаётся последняя `measured_at`:
   - Если пусто — берём последние `LOOKBACK_HOURS_INITIAL` часов
   - Если есть — берём `last_ts - 10 минут` (на случай обновления последней точки)
4. Вызывается `client.get_heart_rate(start, now)`, которая запускает Node.js скрипт.
5. Полученные точки вставляются в БД через `upsert_entries` (дублирующиеся `measured_at` пропускаются).
6. Обновляется `profile_name` у последней записи (для поддержки нескольких аккаунтов).

### 2. NodeGarminClient (`worker/garmin_client.py`)

Обёртка, которая через `subprocess.run` запускает `worker_node/fetch_heart_rate.js`:

```python
cmd = ["node", NODE_HELPER_SCRIPT, start.isoformat(), end.isoformat()]
result = subprocess.run(cmd, cwd=NODE_HELPER_DIR, env=env,
                        capture_output=True, text=True, timeout=120)
```

**Как работает:**
- Креды передаются через `env` в Node процесс.
- Node скрипт возвращает JSON в файл `_result.json` с полями `{profile_name, entries}`.
- Python парсит результат, ISO-строки превращаются в `datetime` (UTC).

### 3. Логин и кэширование токенов (Node helper)

**Проблема:** Garmin Connect защищён Cloudflare. Python-клиенты блокируются по TLS fingerprint'у. Node.js библиотека `garmin-connect` проходит как обычный браузер.

**Решение:** Весь логин — только в Node helper (`worker_node/fetch_heart_rate.js`):

1. **Первый запуск:**
   - `client.login(username, password)` → OAuth2 access + refresh tokens
   - Токены сохраняются в `worker_node/tokens/`
   
2. **Последующие запуски:**
   - `client.loadTokenByFile()` → валидные токены восстанавливаются
   - Нет нужды логиниться заново, Cloudflare не видит чувствительный `/login` endpoint
   - Refresh token живёт примерно год, поэтому переlogин редкий

3. **Если токены устарели:**
   - Автоматический fallback на полный логин
   - Node пишет результат (успех/ошибка) в результирующий файл

### 4. Backend (`backend/app/`)

- `main.py` — FastAPI app, CORS, роутер
- `database.py` — SQLite engine с абсолютным путём (важно: worker и backend должны видеть одну БД)
- `models.py` — модель `BodyBatteryLog` с полями: `id`, `measured_at` (UNIQUE), `level`, `fetched_at`, `profile_name`
- `api.py` — все эндпоинты (см. раздел API ниже)

### 5. Frontend (`frontend/`)

- `index.html` — интерфейс с текущим пульсом, историческим графиком и управлением логинами.
- `app.js`:
  - `fetchCurrent` + `fetchHistory` параллельно через `Promise.all`,
  - `setInterval(load, 5 * 60 * 1000)` — автообновление каждые 5 минут,
  - **цветовые зоны по BPM:**
    - `zone-green`: 50–90 (норма покоя),
    - `zone-yellow`: 90–130 (умеренная нагрузка),
    - `zone-red`: < 50 или > 130,
  - график обновляется in-place (не пересоздаётся) для плавности,
  - кнопка "Get Now" для немедленного обновления данных через API `/api/v1/refresh`,
  - управление логинами (вход/выход из Garmin Connect).
- `styles.css` — стили для зон пульса и элементов управления.

---

## Локальный запуск (без Docker)

### Предварительно

- Python 3.11+
- Node.js 18+ (для Garmin helper)
- Один или несколько аккаунтов Garmin Connect

---

## 🚀 БЫСТРЫЙ ЗАПУСК

### Шаг 1: Установить зависимости

```bash
# Backend
cd backend && pip install -r requirements.txt

# Worker
cd ../worker && pip install -r requirements.txt

# Node helper
cd ../worker_node && npm install
```

### Шаг 2: Запустить все сервисы

```bash
# 1. Backend (порт 8000)
cd backend && python -m uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!

# 2. Frontend (порт 5500)
cd ../frontend && python -m http.server 5500 &
FRONTEND_PID=$!

# 3. Worker (обновляет БД каждые 5 минут)
cd ../worker && python -m worker.worker &
WORKER_PID=$!

echo "✅ Backend PID: $BACKEND_PID"
echo "✅ Frontend PID: $FRONTEND_PID"
echo "✅ Worker PID: $WORKER_PID"
echo ""
echo "🌐 Фронтенд: http://127.0.0.1:5500"
echo "📡 Бэкенд: http://127.0.0.1:8000"
echo ""
echo "❌ Чтобы остановить все процессы:"
echo "kill $BACKEND_PID $FRONTEND_PID $WORKER_PID"
```

---

## Детальный запуск

### 1. Backend

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

Бэкенд запустится на **http://127.0.0.1:8000**

### 2. Worker (фоновый scheduler)

```bash
cd worker
pip install -r requirements.txt

# Перед первым запуском установить Node.js зависимости
cd ../worker_node && npm install && cd ../worker

# Запустить worker (по умолчанию интервал 5 минут)
python -m worker.worker

# Или с пользовательским интервалом
POLL_MINUTES=10 python -m worker.worker
```

**Первый запуск:** полный логин (~5–10 секунд), токены сохранятся в `worker_node/tokens/`. Следующие запуски будут мгновенными (используются сохранённые токены).

**Worker обновляет БД автоматически каждые N минут.**

### 3. Frontend

```bash
cd frontend
python -m http.server 5500
```

Фронтенд откроется на **http://127.0.0.1:5500**

---

## Переменные окружения

| Переменная | Где | По умолчанию | Назначение |
|------------|-----|--------------|------------|
| `DATABASE_URL` | backend, worker | `sqlite:///<repo>/data/body_battery.db` | SQLAlchemy URL |
| `POLL_MINUTES` | worker | `5` | Интервал опроса (минуты) |
| `LOOKBACK_HOURS_INITIAL` | worker | `6` | Сколько часов забрать при первом запуске |

**Примечание:** Credentials загружаются из `backend/data/credentials.json` (сохраняются через API `/api/v1/login`), не через env переменные.

---

## API

### `GET /api/v1/battery/current`

Текущий пульс с метаданными:

```json
{
  "timestamp": "2026-04-08T09:44:00",
  "level": 61,
  "status": "decreasing",
  "minutes_since_update": 7,
  "is_stale": false
}
```

- `status`: `decreasing` / `increasing` / `stable` / `unknown` (на основе последних 3 точек)
- `is_stale`: `true`, если последнее обновление >15 минут назад

### `GET /api/v1/battery/history?hours=24`

История пульса за N часов (1–168 часов):

```json
{
  "period_hours": 24,
  "data": [
    {"time": "2026-04-07T10:00:00", "level": 65},
    {"time": "2026-04-07T10:02:00", "level": 64}
  ]
}
```

### `POST /api/v1/login`

Логин в Garmin Connect:

```json
{
  "username": "user@example.com",
  "password": "password"
}
```

### `POST /api/v1/logout`

Выход из аккаунта:

```json
{}
```

### `POST /api/v1/refresh`

Немедленное обновление данных (запускает worker):

```json
{}
```

**Ответ:** `{"status": "refresh_triggered"}`

### `GET /api/v1/config`

Информация о конфигурации и статусе:

```json
{
  "poll_minutes": 5,
  "lookback_hours_initial": 6,
  "is_logged_in": true,
  "current_username": "user@example.com"
}
```

---

## Схема БД

Таблица `body_battery_logs`:
| Поле | Тип | Примечание |
|------|-----|-----------|
| `id` | Integer | Primary Key |
| `measured_at` | DateTime(tz) | UNIQUE INDEX, UTC |
| `level` | SmallInteger | BPM (пульс) |
| `fetched_at` | DateTime(tz) | Когда запись добавлена в БД |
| `profile_name` | String | Имя профиля Garmin (для разных аккаунтов) |

---

## Тестирование

Проект включает 56 тестов с использованием pytest. CI запускается через GitHub Actions на каждый push.

**Запустить локально:**
```bash
pytest tests/ -v
```

**Покрытие:**
- `test_api.py` — API endpoints, логин/логаут, refresh
- `test_garmin_client.py` — NodeGarminClient, парсинг результатов
- `test_worker.py` — Worker логика, upsert, scheduler
- `conftest.py` — fixtures для БД и клиентов

---

## Docker Compose

```bash
docker-compose up
```

Запустит все три сервиса (backend, worker, worker_node) в контейнерах.

**Примечание:** Убедитесь, что `credentials.json` существует, прежде чем стартовать (создаётся при первом логине через фронтенд).

---

## Файлы конфигурации

- `backend/data/credentials.json` (gitignored) — сохранённые username/password
- `worker_node/tokens/` (gitignored) — OAuth2 токены из Garmin
- `data/body_battery.db` (gitignored) — SQLite БД

---

## Известные ограничения

- API URL во фронте захардкожен (`http://localhost:8000`) — нужно изменить при развёртывании на другом хосте.
- Метрика сейчас **Heart Rate** (пульс). Расширение требует изменений в Node helper (`fetch_heart_rate.js`) и моделях БД.
