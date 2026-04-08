# Garmin Recovery Dashboard

Легковесный дашборд для мониторинга Body Battery на основе данных Garmin.

## Архитектура

- `worker/` — сбор данных из неофициального Garmin API
- `backend/` — FastAPI + SQLite API
- `frontend/` — статическая страница с визуализацией Chart.js
- `docker-compose.yml` — запуск backend и worker

## Быстрый старт

1. Установите зависимости:
   - backend: `pip install -r backend/requirements.txt`
   - worker: `pip install -r worker/requirements.txt`
2. Запустите сервисы:
   - `docker compose up --build`

## API

- `GET /api/v1/battery/current` — текущий уровень Body Battery
- `GET /api/v1/battery/history?hours=24` — история за последние 24 часа

## Структура данных

- `body_battery_logs`:
  - `id`
  - `measured_at` (UTC, уникальный)
  - `level`
  - `fetched_at`

## Плейсхолдеры

- Для production добавьте реальный Garmin-клиент в `worker/garmin_client.py`
- Для удобства запуска фронтенда можно открыть `frontend/index.html` напрямую или через любой статический сервер
