# Task Platform — Distributed Job Queue + Observability

<img width="1024" height="572" alt="image" src="https://github.com/user-attachments/assets/908f21f6-5d55-4a6a-9a58-6c902ef5bcf8" />


# Task Platform — Distributed Job Queue + Observability

A distributed **asynchronous job-processing** platform where clients submit tasks to an API, tasks are processed by **scaled workers** from a **durable RabbitMQ queue**, failures are **retried** and then **dead-lettered (DLQ)**, and everything is observable via **Prometheus + Grafana**. Runs locally with **Docker Compose**.

## Highlights (What this proves)
- **At-least-once processing** (ACK only after durable DB updates)
- **Retries + DLQ** for poison messages
- **Persistent task lifecycle** in Postgres (`QUEUED → PROCESSING → SUCCEEDED/FAILED`)
- **Operational observability** (rates + per-queue depth dashboards)
- **Measured scaling** (1 worker vs 3 workers)

## Measured Results (2,000-task end-to-end load test)
- **1 worker:** processed **2,000 tasks in 64s** → **31.25 tasks/sec**
- **3 workers:** **~41.7 tasks/sec** (warm run) → **~+33% throughput** vs 1 worker

---

## Architecture

mermaid
flowchart LR
  C[Client] -->|POST /tasks| A[FastAPI API]
  A -->|publish msg| Q[(RabbitMQ: tasks)]
  Q --> W1[Worker 1]
  Q --> W2[Worker 2]
  Q --> W3[Worker 3]
  W1 -->|state updates| DB[(Postgres)]
  W2 -->|state updates| DB
  W3 -->|state updates| DB
  W1 -->|exhausted retries| DLQ[(RabbitMQ: tasks.dlq)]
  W2 -->|exhausted retries| DLQ
  W3 -->|exhausted retries| DLQ

  P[Prometheus] -->|scrape| A
  P -->|scrape| W1
  P -->|scrape| W2
  P -->|scrape| W3
  P -->|scrape per-queue| R[RabbitMQ metrics /metrics/per-object]
  G[Grafana] -->|dashboards| P
Queues

tasks (durable main queue)

tasks.dlq (dead-letter queue for permanent failures)

Tech Stack
API: FastAPI (Python)

Queue/Broker: RabbitMQ (durable queue + DLQ)

Database: Postgres (persistent task state)

Cache: Redis (included for caching / future idempotency improvements)

Observability: Prometheus + Grafana

Deployment: Docker Compose

Core Semantics
Idempotency (Submission Dedup)
Clients can safely retry submissions using:

Idempotency-Key: <key>

If the same key is submitted again, the API returns the existing task rather than creating a duplicate.

At-least-once Processing
Workers ACK messages only after the task state is durably written to Postgres.
If a worker crashes before ACK, RabbitMQ will redeliver → no silent loss.

Retries + DLQ
Each job has max_retries

Worker increments retry count on failure and re-queues until exhausted

After retries are exhausted, the job is routed to tasks.dlq and recorded as permanently failed in Postgres

Quickstart
1) Start the stack
bash
-
docker compose up -d --build
docker compose ps
2) Open UIs
API: http://localhost:8000

Prometheus: http://localhost:9090

Grafana: http://localhost:3000 (admin / admin)

RabbitMQ UI: http://localhost:15672 (guest / guest)

API Usage
Submit a “good” task
bash
-
curl -s -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-good-1" \
  -d '{"task_type":"sum","payload":{"numbers":[1,2,3,4,5]},"max_retries":2}'
Submit a “bad” task (forces retries → DLQ)
bash
-
curl -s -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-bad-1" \
  -d '{"task_type":"nope","payload":{"x":1},"max_retries":2}'
Metrics & Observability
Metrics Endpoints
API metrics: http://localhost:8000/metrics

Worker metrics: exposed per worker container (scraped internally)

RabbitMQ per-queue metrics (IMPORTANT):

http://rabbitmq:15692/metrics/per-object

Use /metrics/per-object so Prometheus receives queue-labeled series (e.g., queue="tasks" and queue="tasks.dlq").

Useful Prometheus Queries
Worker target health (when scaled):

promql
-
up{job="worker"}
Total processed across all workers:

promql
-
sum(worker_tasks_processed_total)
Per-minute rates:

promql
-
60 * rate(worker_tasks_processed_total[1m])
60 * rate(worker_tasks_failed_total[1m])
60 * rate(worker_tasks_retried_total[1m])
Queue depth (per queue):

promql
-
rabbitmq_queue_messages_ready{queue="tasks"}
rabbitmq_queue_messages_ready{queue="tasks.dlq"}
Debug Prometheus Targets / Config
bash
-
curl -s http://localhost:9090/api/v1/targets | head
curl -s http://localhost:9090/api/v1/status/config | head
Grafana Dashboards
Dashboards focus on system behavior, not raw counters.

Rate Panels (Time series)
Processed/min: 60 * rate(worker_tasks_processed_total[1m])

Failed/min: 60 * rate(worker_tasks_failed_total[1m])

Retried/min: 60 * rate(worker_tasks_retried_total[1m])

Queue Depth Panels (Stat)
Tasks queue: rabbitmq_queue_messages_ready{queue="tasks"}

DLQ queue: rabbitmq_queue_messages_ready{queue="tasks.dlq"}

Scaling Workers
Scale to 3 workers:

bash
-
docker compose up -d --scale worker=3 --force-recreate worker
docker compose ps | grep worker
Scale back to 1:

bash
-
docker compose up -d --scale worker=1 --force-recreate worker
Benchmark (2,000 tasks)
This project separates:

API submit rate (how fast you can POST)

End-to-end worker throughput (what actually completes)

End-to-end benchmark script (client-side submit + queue drain wait)
bash
-
RUN=$(date +%s)
N=2000
T0=$(date +%s)

for i in $(seq 1 $N); do
  curl -s -X POST http://localhost:8000/tasks \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: bench-$RUN-$i" \
    -d '{"task_type":"sum","payload":{"numbers":[1,2,3,4,5]},"max_retries":2}' >/dev/null
done

# Wait for main queue to drain (DLQ may stay non-zero if you submitted bad tasks)
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
n=$N
print("elapsed_sec:", elapsed)
print("approx_end_to_end_tasks_per_sec:", n / max(1, elapsed))
PY
Reliability Proof (Retries + DLQ)
To validate failure handling:

Submit an invalid task_type with max_retries=2

Confirm retries increase:

worker_tasks_retried_total

Confirm DLQ depth increases after retries exhausted:

rabbitmq_queue_messages_ready{queue="tasks.dlq"}

Confirm Postgres reflects permanent failure state

This proves both success and failure paths behave predictably under load.

Project Structure
text
-
task-platform/
  api/                    # FastAPI service (POST /tasks, idempotency, metrics)
  worker/                  # worker consumers (retries, DLQ, metrics)
  observability/
    prometheus.yml         # scrape config
    rabbitmq.conf          # RabbitMQ metrics plugin config (if needed)
  docker-compose.yml
Common Gotchas / Fixes
RabbitMQ metrics show no per-queue labels
Make sure Prometheus scrapes:

http://rabbitmq:15692/metrics/per-object

Prometheus worker targets stale after scaling/recreate
bash
-
docker compose restart prometheus
DLQ non-zero is expected
If you intentionally sent poison tasks, tasks.dlq may remain non-zero until you purge it or handle DLQ messages.

Roadmap (Next Improvements)
Add OpenTelemetry tracing (API → queue → worker → DB)

Add worker-side idempotency (exactly-once-ish handler semantics)

Add auth + rate limiting on the API

Add outbox pattern for publish/commit atomicity

Add SLO panels + alerts (p95 queue wait, error budget)
up{job="worker"}

If Prometheus shows stale worker targets after scaling/recreate:

docker compose restart prometheus
