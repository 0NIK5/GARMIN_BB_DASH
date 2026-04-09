# Garmin Recovery Dashboard

Лёгковесный дашборд для мониторинга метрик Garmin (на текущем этапе — Heart Rate, в дальнейшем Body Battery). Берёт данные из неофициального Garmin Connect API, складывает в SQLite и показывает в браузере с автообновлением.

```
Garmin Connect ──▶ worker (Python + Node helper) ──▶ SQLite ──▶ FastAPI ──▶ Frontend
```

---

## Структура проекта

```
GARMIN_BB_DASH/
├── backend/                 # FastAPI + SQLAlchemy 2.0 + Pydantic V2
│   └── app/
│       ├── main.py          # Точка входа, CORS, роутер
│       ├── database.py      # Engine + sessionmaker, абсолютный путь к ../data/body_battery.db
│       ├── models.py        # SQLAlchemy модель BodyBatteryLog
│       ├── schemas.py       # Pydantic-схемы (model_config = from_attributes)
│       ├── crud.py          # get_latest_log / get_history / upsert
│       └── api.py           # /api/v1/battery/current, /battery/history
├── worker/                  # Python-воркер
│   ├── worker.py            # APScheduler BlockingScheduler + retry + dispatcher клиентов
│   └── garmin_client.py     # GarminClient (Python), NodeGarminClient (subprocess), MockGarminClient
├── worker_node/             # Node.js helper для обхода Cloudflare
│   ├── fetch_heart_rate.js  # Логин через garmin-connect, кэш токенов, JSON в stdout
│   ├── package.json
│   └── tokens/              # OAuth-токены (gitignored)
├── frontend/                # Vanilla JS + Chart.js
│   ├── index.html
│   ├── app.js               # Polling 5 мин, цветовые зоны, обновление графика in-place
│   └── styles.css
├── data/                    # SQLite БД (gitignored), общий между backend и worker
└── docker-compose.yml
```

---

## Как это работает

### 1. Worker (`worker/worker.py`)

`BlockingScheduler` запускает `run_job` сразу при старте, потом каждые `POLL_MINUTES` минут (по умолчанию 5).

В `run_job`:
1. По env-переменной `GARMIN_CLIENT` выбирается клиент:
   - `node` — гибридный через Node.js (по умолчанию, обходит Cloudflare)
   - `python` — прямой `garminconnect` (сейчас блокируется CF)
   - `mock` — генератор синтетических данных для разработки без API
2. Вызывается `client.login()`.
3. Из БД достаётся последняя `measured_at`. Если пусто — берём `LOOKBACK_HOURS_INITIAL` часов назад. Если есть — `last_ts - 10 минут` (на случай дозаписи последней точки).
4. `fetch_with_retry(client, start, now)` — обёртка с retry-логикой:
   - 3 попытки с exponential backoff `5s / 15s / 30s` для network errors;
   - 1 повторный логин при 401/403;
5. Полученные точки идут в `upsert_entries`: вставляются только новые `measured_at`.

### 2. Garmin клиенты (`worker/garmin_client.py`)

#### `NodeGarminClient` — рабочий вариант

Класс-обёртка, которая через `subprocess.run` запускает `worker_node/fetch_heart_rate.js`:

```python
cmd = ["node", NODE_HELPER_SCRIPT, start.isoformat(), end.isoformat()]
result = subprocess.run(cmd, cwd=NODE_HELPER_DIR, env=env,
                        capture_output=True, text=True, timeout=120)
```

- Креды передаются через env (`GARMIN_USERNAME` / `GARMIN_PASSWORD`).
- stdout Node-скрипта — это чистый JSON-массив `[{measured_at, level}, ...]`. Любой лог Node пишет в stderr, чтобы не ломать парсинг.
- Полученный JSON парсится, ISO-строки превращаются в `datetime` (UTC).

#### `GarminClient` — Python-вариант (нерабочий из-за CF)

Использует `garminconnect`. Логика логина та же, что в Node-helper'е (см. ниже), но на практике CF возвращает 429 — оставлен для истории/возможного будущего фикса.

#### `MockGarminClient`

Без сети, генерирует правдоподобный пульс:
- суточный синус 60–80 bpm,
- шум ±5,
- редкие всплески до 130–150 (имитация нагрузки, ~3% точек),
- семплирование RNG по `int(timestamp/120)` — одна и та же точка во времени всегда даёт одно и то же значение.

### 3. Логин в Garmin Connect — детально

Garmin Connect защищён Cloudflare. Если стучаться "в лоб" из Python (`requests`/`garth`/`garminconnect`), CF по TLS-fingerprint'у отдаёт **HTTP 429** ещё до любой проверки логина — даже с правильными кредами и сменой IP. Node-библиотека `garmin-connect` использует другой TLS handshake (Node OpenSSL вместо Python ssl/urllib3) и проходит как обычный браузер.

Поэтому реальный login делается **только в Node helper**. Дальше всё построено вокруг **OAuth2 refresh token'а Garmin'а**, который живёт ~год — то есть после первого успешного логина в `/login` endpoint мы больше не ходим вообще, и Cloudflare нас не видит на этом самом чувствительном пути.

#### Полный флоу первого запуска

1. Python `worker.py` → `run_job()` → `NodeGarminClient.get_heart_rate(start, end)`.
2. Запускается `node worker_node/fetch_heart_rate.js <start_iso> <end_iso>`. Node наследует env (включая `GARMIN_USERNAME` / `GARMIN_PASSWORD`).
3. Node-скрипт создаёт `worker_node/tokens/` если её нет.
4. **Попытка №1 — токен-кэш:**
   ```js
   client.loadTokenByFile(TOKEN_DIR);
   await client.getUserProfile();   // лёгкий probe — валидны ли токены
   ```
   Если файла нет или `getUserProfile()` упал — переходим к шагу 5. Если ок — `usedTokens=true`, идём дальше к получению heart rate (шаг 7).
5. **Попытка №2 — полный логин:**
   ```js
   await client.login(username, password);
   ```
   Внутри `garmin-connect` это:
   - GET `https://sso.garmin.com/sso/signin?...` (получить CSRF-токен и cookie),
   - POST `/sso/signin` с email+password+CSRF (тут CF чаще всего и режет Python-клиенты),
   - редирект-цепочка `embed → garmin connect`,
   - обмен ticket'а на OAuth1 access token,
   - обмен OAuth1 на **OAuth2** access + refresh token (используются для всех последующих API-запросов).
6. **Сохранение токенов:** `client.exportTokenToFile(TOKEN_DIR)` пишет в `worker_node/tokens/` пару файлов с `oauth1_token` и `oauth2_token` (включая `refresh_token`).
7. Получение данных за каждый день диапазона: `client.getHeartRate(cdate)` использует уже OAuth2 Bearer-token из памяти, эндпоинт `/wellness-service/wellness/dailyHeartRate/...`. Это публичные API garmin connect, CF их не блочит.
8. Точки фильтруются по `[start, end]`, выводятся в stdout как JSON, Node завершается с кодом 0.

#### Все последующие запуски (steady state)

1. `loadTokenByFile` находит файлы в `worker_node/tokens/`.
2. `getUserProfile()` проходит — токены валидны.
3. `usedTokens=true`, **никакого `/login` не делаем вообще** — Cloudflare на login-эндпоинте даже не видит наш трафик.
4. Сразу идём за heart rate.

В лог Node это пишет так:
```
[node] Logged in via saved tokens
[node] Fetching heart rate for 2026-04-08
[node] Fetched 12 heart rate points (usedTokens=true)
```

#### Если токены устарели / отозваны

- `getUserProfile()` упадёт → срабатывает fallback на `client.login(username, password)` → новые токены → `exportTokenToFile`.
- Если и `client.login` упал — Node пишет `LOGIN_FAILED: ...` в stderr и выходит с кодом 3. Python видит ненулевой returncode и поднимает `RuntimeError`, который ловится `fetch_with_retry`.
- На уровне Python `fetch_with_retry` сделает один повторный логин при `GarminConnectAuthenticationError` — но при работе через Node helper эта ветка по факту не нужна, retry уже происходит внутри `garmin-connect`.

#### Почему refresh token Garmin'а живёт так долго

Garmin использует OAuth2 с очень длинным refresh token'ом (порядка года). Это и спасает: чувствительный `/login` endpoint мы дёргаем максимум один раз за всю жизнь worker'а. Все остальные запросы идут на data-эндпоинты, которые Cloudflare не фильтрует так жёстко.

### 4. Backend (`backend/app/`)

- `database.py` — SQLite, путь `<repo>/data/body_battery.db` высчитывается от `__file__` (абсолютный) — это важно, потому что worker запускается из `worker/`, а backend из `backend/`, и если использовать относительный путь, они откроют **разные файлы БД**.
- `models.py` — таблица `body_battery_logs (id, measured_at UNIQUE INDEX, level, fetched_at)`.
- `crud.py`:
  - `get_latest_log` — `ORDER BY measured_at DESC LIMIT 1`,
  - `get_history(hours)` — точки за последние N часов,
  - `upsert_log` — INSERT с фильтром по `measured_at` (но воркер делает свой upsert).
- `api.py`:
  - `GET /api/v1/battery/current` → `{timestamp, level, status, minutes_since_update, is_stale}`. `status` = `decreasing` / `increasing` / `stable` / `unknown` (по последним 3 точкам). `is_stale` = `True`, если `minutes_since_update > 15`.
  - `GET /api/v1/battery/history?hours=N` (1 ≤ N ≤ 168) → `{period_hours, data:[{time, level}, ...]}`.

### 5. Frontend (`frontend/`)

- `index.html` — два блока: текущий пульс и график.
- `app.js`:
  - `fetchCurrent` + `fetchHistory` параллельно через `Promise.all`,
  - `setInterval(load, 5 * 60 * 1000)` — обновление каждые 5 минут,
  - **цветовые зоны по BPM:**
    - `zone-green`: 50–90 (норма покоя),
    - `zone-yellow`: 90–130 (умеренная нагрузка),
    - `zone-red`: < 50 или > 130,
  - график обновляется in-place (не пересоздаётся), чтобы не было мигания.
- `styles.css` — `.zone-green/yellow/red` для текущего значения и точек на графике.

---

## Локальный запуск (без Docker)

### 0. Предварительно

- Python 3.11+
- Node.js 18+ (для Garmin helper)
- Garmin Connect аккаунт

---

## 🚀 БЫСТРЫЙ ЗАПУСК (скопируй в bash)

```bash
# 1. Backend (порт 8000)
cd backend && python -m uvicorn app.main:app --reload --port 8000 &
BACKEND_PID=$!

# 2. Frontend (порт 5500)
cd ../frontend && python -m http.server 5500 &
FRONTEND_PID=$!

# 3. Worker (обновляет БД каждые 10 минут)
cd ../worker && POLL_MINUTES=10 python -m worker.worker &
WORKER_PID=$!

echo "Backend PID: $BACKEND_PID"
echo "Frontend PID: $FRONTEND_PID"
echo "Worker PID: $WORKER_PID"
echo ""
echo "Фронтенд: http://127.0.0.1:5500"
echo "Бэкенд: http://127.0.0.1:8000"
echo ""
echo "Чтобы остановить все процессы:"
echo "kill $BACKEND_PID $FRONTEND_PID $WORKER_PID"
```

**Перед первым запуском:**

```bash
# Установить зависимости
cd backend && pip install -r requirements.txt
cd ../worker && pip install APScheduler==3.10.2 sqlalchemy==2.0.40 requests==2.31.0 python-dateutil==2.8.2
cd ../worker_node && npm install
```

---

## Детальный запуск

### 1. Backend

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```

Откроется на **http://127.0.0.1:8000**

### 2. Worker (фоновый scheduler)

```bash
cd worker
pip install APScheduler==3.10.2 sqlalchemy==2.0.40 requests==2.31.0 python-dateutil==2.8.2

# Перед первым запуском установить Node.js зависимости
cd ../worker_node && npm install && cd ../worker

# Запустить worker с интервалом 10 минут
POLL_MINUTES=10 python -m worker.worker
```

При первом запуске будет полный логин (~5–10 секунд), потом токены лягут в `worker_node/tokens/` и следующие запуски будут мгновенными. **Worker обновляет БД автоматически каждые N минут.**

### 3. Frontend

```bash
cd frontend
python -m http.server 5500
```

Откроется на **http://127.0.0.1:5500**

### Mock-режим (без интернета)

```bash
GARMIN_CLIENT=mock python worker.py
```

---

## Переменные окружения

| Переменная | Где | По умолчанию | Назначение |
|------------|-----|--------------|------------|
| `DATABASE_URL` | backend, worker | `sqlite:///<repo>/data/body_battery.db` | URL SQLAlchemy |
| `GARMIN_USERNAME` | worker, node | — | email Garmin Connect |
| `GARMIN_PASSWORD` | worker, node | — | пароль Garmin Connect |
| `GARMIN_CLIENT` | worker | `node` (или `mock` если `USE_MOCK=1`) | `node` / `python` / `mock` |
| `USE_MOCK` | worker | `0` | shortcut: `1` → mock |
| `POLL_MINUTES` | worker | `5` | интервал опроса |
| `LOOKBACK_HOURS_INITIAL` | worker | `6` | сколько часов забрать при пустой БД |

---

## API

### `GET /api/v1/battery/current`

```json
{
  "timestamp": "2026-04-08T09:44:00",
  "level": 61,
  "status": "decreasing",
  "minutes_since_update": 7,
  "is_stale": false
}
```

### `GET /api/v1/battery/history?hours=24`

```json
{
  "period_hours": 24,
  "data": [
    {"time": "2026-04-07T10:00:00", "level": 65},
    {"time": "2026-04-07T10:02:00", "level": 64}
  ]
}
```

---

## Схема БД

`body_battery_logs`:
- `id` — PK
- `measured_at` — `DateTime(timezone=True)`, UNIQUE INDEX
- `level` — `SmallInteger` (BPM в текущей heart-rate-конфигурации)
- `fetched_at` — `DateTime(timezone=True)`

---

## Известные ограничения / TODO

- Python-клиент `garminconnect` блокируется Cloudflare. Оставлен в коде, но в проде используется Node helper.
- Нет healthcheck'а в `docker-compose.yml` — worker может стартануть раньше, чем backend инициализирует БД (на практике не страшно, потому что worker сам создаёт таблицы).
- API URL во фронте захардкожен (`http://localhost:8000`).
- Метрика сейчас Heart Rate (для отладки). Переезд на Body Battery — заменить `getHeartRate` в Node helper'е и поле `level` останется тем же.
