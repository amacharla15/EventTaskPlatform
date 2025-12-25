# worker/worker.py
import json
import time
from datetime import datetime

import pika
from prometheus_client import Counter, start_http_server
from sqlalchemy import Column, String, DateTime, Integer, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import create_engine

DATABASE_URL = "postgresql+psycopg2://task:task@postgres:5432/taskdb"
RABBIT_URL = "amqp://guest:guest@rabbitmq:5672/"
QUEUE_NAME = "tasks"
DLQ_NAME = "tasks.dlq"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class Task(Base):
    __tablename__ = "tasks"
    id = Column(String, primary_key=True)
    idempotency_key = Column(String, unique=True, nullable=True)
    status = Column(String, nullable=False)
    task_type = Column(String, nullable=False)
    payload = Column(Text, nullable=False)
    result = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    retries = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

worker_processed = Counter("worker_tasks_processed_total", "Worker tasks processed (succeeded)")
worker_failed = Counter("worker_tasks_failed_total", "Worker tasks failed permanently (DLQ)")
worker_retried = Counter("worker_tasks_retried_total", "Worker task retries (re-enqueued)")

def process_task(task_type: str, payload: dict) -> dict:
    if task_type == "sum":
        arr = payload.get("numbers", [])
        s = 0
        for x in arr:
            s += int(x)
        return {"sum": s, "count": len(arr)}

    if task_type == "sleep":
        secs = float(payload.get("seconds", 1))
        time.sleep(secs)
        return {"slept": secs}

    raise RuntimeError("unknown task_type")

def publish(ch, routing_key: str, body: dict):
    ch.queue_declare(queue=routing_key, durable=True)
    ch.basic_publish(
        exchange="",
        routing_key=routing_key,
        body=json.dumps(body).encode("utf-8"),
        properties=pika.BasicProperties(delivery_mode=2),
    )

def main():
    start_http_server(8001)
    Base.metadata.create_all(bind=engine)

    params = pika.URLParameters(RABBIT_URL)
    conn = pika.BlockingConnection(params)
    ch = conn.channel()

    ch.queue_declare(queue=QUEUE_NAME, durable=True)
    ch.queue_declare(queue=DLQ_NAME, durable=True)
    ch.basic_qos(prefetch_count=1)

    def on_message(channel, method, properties, body_bytes):
        msg = json.loads(body_bytes.decode("utf-8"))
        task_id = msg["task_id"]
        attempt = int(msg.get("attempt", 0))

        db = SessionLocal()
        try:
            row = db.query(Task).filter(Task.id == task_id).first()
            if row is None:
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            row.status = "RUNNING"
            row.updated_at = datetime.utcnow()
            db.commit()

            ok = False
            result = None
            err = None

            try:
                payload = json.loads(row.payload)
                result = process_task(row.task_type, payload)
                ok = True
            except Exception as e:
                err = str(e)

            if ok:
                row.status = "SUCCEEDED"
                row.result = json.dumps(result)
                row.error = None
                row.updated_at = datetime.utcnow()
                db.commit()
                worker_processed.inc()
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            next_attempt = attempt + 1
            row.retries = next_attempt
            row.error = err
            row.updated_at = datetime.utcnow()

            if next_attempt <= row.max_retries:
                row.status = "QUEUED"
                db.commit()
                worker_retried.inc()
                publish(channel, QUEUE_NAME, {"task_id": task_id, "attempt": next_attempt})
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            row.status = "FAILED"
            db.commit()
            worker_failed.inc()
            publish(channel, DLQ_NAME, {"task_id": task_id, "attempt": next_attempt, "error": err})
            channel.basic_ack(delivery_tag=method.delivery_tag)
        finally:
            db.close()

    ch.basic_consume(queue=QUEUE_NAME, on_message_callback=on_message)
    print("worker consuming queue:", QUEUE_NAME, flush=True)
    ch.start_consuming()

if __name__ == "__main__":
    main()
