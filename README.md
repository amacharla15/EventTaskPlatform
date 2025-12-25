# Task Platform — Distributed Job Queue + Observability

A small distributed task-processing platform built to demonstrate **reliable async jobs**, **failure handling (DLQ)**, and **real metrics-driven observability** using **FastAPI + RabbitMQ + Postgres + Prometheus + Grafana** (Docker Compose).

**Measured throughput (2,000-task load test):**
- **31.25 tasks/sec** with **1 worker**
- **41.7 tasks/sec** with **3 workers** (**~+33%** throughput improvement)

---

## Architecture (Pipeline)

**FastAPI Task Submission API → RabbitMQ Durable Queue → Worker Consumers (scaled) → Postgres Task Lifecycle**  
**Failure Handling + DLQ** for tasks that exceed retries  
**Prometheus + Grafana** for metrics, queue depth, and rate panels

Core components:
- **API (FastAPI)**: `POST /tasks`, idempotency-based deduplication, validates payload, persists initial task row in Postgres
- **RabbitMQ**: durable queue `tasks`, dead-letter queue `tasks.dlq`
- **Workers**: ack on success, retry on failure (max retries), update Postgres task status
- **Postgres**: task lifecycle state machine `QUEUED → PROCESSING → SUCCEEDED/FAILED`
- **Observability**: Prometheus scrapes API + worker metrics + RabbitMQ per-queue metrics (`/metrics/per-object`), Grafana dashboards

---

## Tech Stack

- **API**: FastAPI (Python)
- **Queue**: RabbitMQ (durable queue + DLQ)
- **DB**: Postgres
- **Cache**: Redis (service included)
- **Observability**: Prometheus + Grafana
- **Infra**: Docker Compose

---

## Features

- **Idempotent task submission** via `Idempotency-Key` header (safe retries from clients)
- **Durable message processing** with RabbitMQ (`tasks` queue)
- **Worker scaling** with Docker Compose (`--scale worker=N`)
- **Retries + DLQ routing** when retries are exhausted (`tasks.dlq`)
- **Task lifecycle persisted** in Postgres for auditability and crash-safe recovery
- **Prometheus + Grafana dashboards** for processed/min, failed/min, retried/min, and queue depth (`tasks`, `tasks.dlq`)

---

## Quickstart

### 1) Start the stack

cd task-platform
docker compose up -d --build
docker compose ps
2) Open UIs
API: http://localhost:8000

Prometheus: http://localhost:9090

Grafana: http://localhost:3000 (admin / admin)

RabbitMQ UI: http://localhost:15672 (guest / guest)

Submit Tasks
Good task
bash
- -
curl -s -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-good-1" \
  -d '{"task_type":"sum","payload":{"numbers":[1,2,3,4,5]},"max_retries":2}'
Bad task (forces failure path / DLQ after retries)
bash
- -
curl -s -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-bad-1" \
  -d '{"task_type":"nope","payload":{"x":1},"max_retries":2}'
Observability
Prometheus queries (examples)
Worker health:

up{job="worker"}

Total processed:

sum(worker_tasks_processed_total)

Queue depth (RabbitMQ per-queue):

rabbitmq_queue_messages_ready{queue="tasks"}

rabbitmq_queue_messages_ready{queue="tasks.dlq"}

RabbitMQ metrics endpoint
Prometheus scrapes per-queue metrics from:

http://rabbitmq:15692/metrics/per-object

Scaling Workers
Scale workers to 3:

bash
- -
docker compose up -d --scale worker=3 --force-recreate worker
docker compose ps | grep worker
Scale back to 1:

bash
- -
docker compose up -d --scale worker=1 --force-recreate worker
Benchmark (2,000 tasks)
Run a 2,000-task load test (client-side)
bash
- -
RUN=$(date +%s)
N=2000
T0=$(date +%s)

for i in $(seq 1 $N); do
  curl -s -X POST http://localhost:8000/tasks \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: bench-$RUN-$i" \
    -d '{"task_type":"sum","payload":{"numbers":[1,2,3,4,5]},"max_retries":2}' >/dev/null
done

# Wait for tasks queue to drain (DLQ may stay non-zero if you sent bad tasks)
while true; do
  OUT=$(docker exec task-platform-rabbitmq-1 rabbitmqctl list_queues name messages_ready messages_unacknowledged | egrep '^(tasks|tasks\.dlq)\s')
  READY=$(echo "$OUT" | awk '$1=="tasks"{print $2}')
  UNACK=$(echo "$OUT" | awk '$1=="tasks"{print $3}')
  if [ "${READY:-1}" = "0" ] && [ "${UNACK:-1}" = "0" ]; then break; fi
  sleep 1
done

T1=$(date +%s)
python3 - <<PY
elapsed=$((T1-T0))
print("elapsed_sec:", elapsed)
print("approx_rate_tasks_per_sec:", $N / max(1, elapsed))
PY
Observed results (my runs):

1 worker: 31.25 tasks/sec

3 workers: 41.7 tasks/sec (~+33%)

Repository Structure
graphql

task-platform/
  api/                 # FastAPI service
  worker/              # worker consumers
  observability/
    prometheus.yml     # Prometheus scrape config
    rabbitmq.conf      # RabbitMQ Prometheus plugin config
  docker-compose.yml
Notes / Gotchas
Prometheus query syntax: use job="worker" inside label braces, e.g. up{job="worker"}.

If Prometheus shows stale worker targets after recreates, restart Prometheus:


docker compose restart prometheus
DLQ (tasks.dlq) may be non-zero if you intentionally submit invalid tasks.
