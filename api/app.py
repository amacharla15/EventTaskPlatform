# api/app.py
import json
import uuid
from datetime import datetime

import pika
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST
from sqlalchemy import Column, String, DateTime, Integer, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import create_engine

DATABASE_URL = "postgresql+psycopg2://task:task@postgres:5432/taskdb"
RABBIT_URL = "amqp://guest:guest@rabbitmq:5672/"
QUEUE_NAME = "tasks"

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

class SubmitRequest(BaseModel):
    task_type: str
    payload: dict
    max_retries: int = 3

app = FastAPI()
tasks_submitted = Counter("tasks_submitted_total", "Total tasks created (unique)")

def rabbit_publish(body: dict) -> None:
    params = pika.URLParameters(RABBIT_URL)
    conn = pika.BlockingConnection(params)
    ch = conn.channel()
    ch.queue_declare(queue=QUEUE_NAME, durable=True)
    ch.basic_publish(
        exchange="",
        routing_key=QUEUE_NAME,
        body=json.dumps(body).encode("utf-8"),
        properties=pika.BasicProperties(delivery_mode=2),
    )
    conn.close()

@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)

@app.get("/metrics")
def metrics():
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)

@app.post("/tasks")
def submit_task(req: SubmitRequest, idempotency_key: str | None = Header(default=None)):
    db = SessionLocal()
    try:
        if idempotency_key is not None:
            existing = db.query(Task).filter(Task.idempotency_key == idempotency_key).first()
            if existing is not None:
                return {"task_id": existing.id, "status": existing.status}

        tasks_submitted.inc()

        task_id = str(uuid.uuid4())
        now = datetime.utcnow()

        row = Task(
            id=task_id,
            idempotency_key=idempotency_key,
            status="PENDING",
            task_type=req.task_type,
            payload=json.dumps(req.payload),
            result=None,
            error=None,
            retries=0,
            max_retries=int(req.max_retries),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.commit()

        rabbit_publish({"task_id": task_id, "attempt": 0})

        row.status = "QUEUED"
        row.updated_at = datetime.utcnow()
        db.commit()

        return {"task_id": task_id, "status": row.status}
    finally:
        db.close()

@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    db = SessionLocal()
    try:
        row = db.query(Task).filter(Task.id == task_id).first()
        if row is None:
            raise HTTPException(status_code=404, detail="task not found")
        return {
            "task_id": row.id,
            "status": row.status,
            "retries": row.retries,
            "max_retries": row.max_retries,
            "task_type": row.task_type,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
    finally:
        db.close()

@app.get("/tasks/{task_id}/result")
def get_result(task_id: str):
    db = SessionLocal()
    try:
        row = db.query(Task).filter(Task.id == task_id).first()
        if row is None:
            raise HTTPException(status_code=404, detail="task not found")
        return {
            "task_id": row.id,
            "status": row.status,
            "result": row.result,
            "error": row.error,
        }
    finally:
        db.close()
