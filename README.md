# Task Platform — Distributed Job Queue + Observability

<img width="1024" height="572" alt="image" src="https://github.com/user-attachments/assets/908f21f6-5d55-4a6a-9a58-6c902ef5bcf8" />


A distributed task-processing platform that demonstrates reliable async job execution, failure handling with retries + DLQ, and metrics-driven observability using FastAPI + RabbitMQ + Postgres + Prometheus + Grafana (Docker Compose).

Measured throughput (2,000-task end-to-end load test):

31.25 tasks/sec with 1 worker (2000 / 64s)

~41.7 tasks/sec with 3 workers (~+33% improvement)

What this project proves (Success Criteria)

Reliable at-least-once processing: messages are only ACKed after durable DB state updates

Retries + DLQ: poison tasks retry up to max_retries, then dead-letter to tasks.dlq

Persistent task lifecycle in Postgres: QUEUED → PROCESSING → SUCCEEDED/FAILED

Observable system: Prometheus + Grafana dashboards for rates + queue depth

Scales with workers: measurable throughput improvement from 1 → 3 workers

Architecture

FastAPI (Task API) → RabbitMQ (durable queue) → Workers (scaled consumers) → Postgres (task state)
Prometheus scrapes API + Workers + RabbitMQ (per-queue) → Grafana dashboards

Queues:

tasks (durable main queue)

tasks.dlq (dead-letter queue for permanently failing tasks)

Tech Stack

API: FastAPI (Python)

Queue: RabbitMQ (durable queue + DLQ)

DB: Postgres

Cache: Redis (included in stack for caching / future idempotency improvements)

Observability: Prometheus + Grafana

Infra: Docker Compose

Key Features

Idempotent submission via Idempotency-Key header (safe client retries without duplicate tasks)

Durable async execution with RabbitMQ queue buffering

At-least-once semantics (ACK only after DB is updated to final state)

Retry + DLQ routing when retries are exhausted

Postgres audit trail with timestamps for queue wait + processing time analysis

Metrics exposed from API + workers on /metrics and from RabbitMQ via per-object metrics

Grafana dashboards showing processed/failed/retried rates and queue depths (tasks, tasks.dlq)

Quickstart
1) Start the stack
cd task-platform
docker compose up -d --build
docker compose ps

2) Open UIs

API: http://localhost:8000

Prometheus: http://localhost:9090

Grafana: http://localhost:3000
 (admin / admin)

RabbitMQ UI: http://localhost:15672
 (guest / guest)

Submit Tasks
Good task
curl -s -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-good-1" \
  -d '{"task_type":"sum","payload":{"numbers":[1,2,3,4,5]},"max_retries":2}'

Bad task (forces retries → DLQ)
curl -s -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-bad-1" \
  -d '{"task_type":"nope","payload":{"x":1},"max_retries":2}'

Observability
Metrics Endpoints

API metrics: http://localhost:8000/metrics

Worker metrics: exposed per worker container (scraped internally by Prometheus)

RabbitMQ per-queue metrics (critical for queue-labeled series):

http://rabbitmq:15692/metrics/per-object

Useful Prometheus Queries

Worker health (when scaled):

up{job="worker"}


Total processed (across all workers):

sum(worker_tasks_processed_total)


Processed per minute:

60 * rate(worker_tasks_processed_total[1m])


Failed per minute:

60 * rate(worker_tasks_failed_total[1m])


Retried per minute:

60 * rate(worker_tasks_retried_total[1m])


Queue depth (per queue):

rabbitmq_queue_messages_ready{queue="tasks"}
rabbitmq_queue_messages_ready{queue="tasks.dlq"}

Debugging Prometheus Targets
curl -s http://localhost:9090/api/v1/targets | head
curl -s http://localhost:9090/api/v1/status/config | head

Grafana Dashboards

This project includes dashboards that show system behavior (not just raw counters):

Rate Panels (Time Series)

Processed/min: 60 * rate(worker_tasks_processed_total[1m])

Failed/min: 60 * rate(worker_tasks_failed_total[1m])

Retried/min: 60 * rate(worker_tasks_retried_total[1m])

Queue Depth (Stat Panels)

rabbitmq_queue_messages_ready{queue="tasks"}

rabbitmq_queue_messages_ready{queue="tasks.dlq"}

Scaling Workers

Scale to 3 workers:

docker compose up -d --scale worker=3 --force-recreate worker
docker compose ps | grep worker


Scale back to 1 worker:

docker compose up -d --scale worker=1 --force-recreate worker


Note: workers should not bind conflicting host ports. They run on internal Docker networking and are scraped by Prometheus inside the Compose network.

Benchmarking (2,000 Tasks)

This project distinguishes:

API submit rate (how fast you can POST)

True end-to-end worker throughput (what actually gets processed)

End-to-end throughput method

Record start value: sum(worker_tasks_processed_total)

Submit N = 2000 tasks

Wait for RabbitMQ queue to drain (tasks ready/unacked → 0)

Record end value and elapsed time

Compute: throughput = processed_delta / elapsed_time

Client-side submit + queue-drain wait
RUN=$(date +%s)
N=2000
T0=$(date +%s)

for i in $(seq 1 $N); do
  curl -s -X POST http://localhost:8000/tasks \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: bench-$RUN-$i" \
    -H "Content-Type: application/json" \
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
print("approx_end_to_end_tasks_per_sec:", $N / max(1, elapsed))
PY

Results (my runs)

1 worker: 31.25 tasks/sec

3 workers: ~41.7 tasks/sec (~+33%)

Reliability Proof (Retries + DLQ)

To validate the failure path:

Submit an invalid task_type with max_retries=2

Confirm retries increase:

worker_tasks_retried_total

Confirm DLQ depth increases after retries exhausted:

rabbitmq_queue_messages_ready{queue="tasks.dlq"}

Confirm Postgres records permanent failure state

This proves predictable behavior under failures, not just “happy path” success.

Repository Structure
task-platform/
  api/                    # FastAPI task submission service
  worker/                 # worker consumers
  observability/
    prometheus.yml        # Prometheus scrape config
    rabbitmq.conf         # RabbitMQ Prometheus plugin / metrics config
  docker-compose.yml

Notes / Gotchas

RabbitMQ queue-labeled metrics require scraping:

http://rabbitmq:15692/metrics/per-object
If you scrape the wrong endpoint, you may only see aggregates without queue="..." labels.

Prometheus query labels must be inside braces, e.g.:

up{job="worker"}

If Prometheus shows stale worker targets after scaling/recreate:

docker compose restart prometheus
