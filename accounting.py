from contextlib import contextmanager
from pathlib import Path
import sqlite3
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field


app = FastAPI(title="Mock Accounting")

# Keep the database next to this file, regardless of the terminal's directory.
DB_PATH = Path(__file__).with_name("accounting.db")
failure_lock = Lock()
remaining_failures = 0


@contextmanager
def database():
    connection = sqlite3.connect(DB_PATH, timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS customers (
                    id TEXT PRIMARY KEY,
                    crm_id TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    invoice_email TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1
                )"""
            )
            # Upgrade the database created during the earlier tutorial steps.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(customers)")}
            if "version" not in columns:
                connection.execute("ALTER TABLE customers ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
            yield connection
    finally:
        connection.close()


class NewCustomer(BaseModel):
    crm_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    invoice_email: str = Field(min_length=1)


class FailureSettings(BaseModel):
    next_writes: int = Field(ge=0, le=10)


class CustomerUpdate(BaseModel):
    display_name: str = Field(min_length=1)
    invoice_email: str = Field(min_length=1)
    expected_version: int = Field(ge=1)


def simulate_write_failure():
    global remaining_failures
    with failure_lock:
        if remaining_failures > 0:
            remaining_failures -= 1
            raise HTTPException(status_code=503, detail="Simulated temporary outage.")


@app.post("/admin/failures", tags=["Demo controls"])
def set_failures(settings: FailureSettings):
    """Local demo only: fail this many customer writes before they reach the database."""
    global remaining_failures
    with failure_lock:
        remaining_failures = settings.next_writes
    return {"next_writes": settings.next_writes}


@app.get("/customers")
def list_customers():
    with database() as connection:
        rows = connection.execute("SELECT * FROM customers ORDER BY crm_id").fetchall()
        return [dict(row) for row in rows]


@app.post("/customers", status_code=201)
def create_customer(customer: NewCustomer, response: Response):
    simulate_write_failure()
    with database() as connection:
        # The UNIQUE constraint prevents duplicate CRM IDs, even with concurrent requests.
        result = connection.execute(
            """INSERT INTO customers (id, crm_id, display_name, invoice_email)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(crm_id) DO NOTHING""",
            (f"acct-{uuid4()}", customer.crm_id, customer.display_name, customer.invoice_email),
        )
        record = dict(connection.execute(
            "SELECT * FROM customers WHERE crm_id = ?", (customer.crm_id,)
        ).fetchone())

        if result.rowcount == 0:
            if any(record[field] != value for field, value in customer.model_dump().items()):
                raise HTTPException(
                    status_code=409,
                    detail="This CRM customer already exists with different details.",
                )
            response.status_code = 200

        return record


@app.get("/customers/{customer_id}")
def get_customer(customer_id: str):
    with database() as connection:
        row = connection.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Customer not found.")
        return dict(row)


@app.patch("/customers/{customer_id}")
def update_customer(customer_id: str, update: CustomerUpdate):
    simulate_write_failure()
    with database() as connection:
        # Check the version and perform the update in one atomic SQL statement.
        result = connection.execute(
            """UPDATE customers SET display_name = ?, invoice_email = ?, version = version + 1
               WHERE id = ? AND version = ?""",
            (update.display_name, update.invoice_email, customer_id, update.expected_version),
        )
        row = connection.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Customer not found.")
        record = dict(row)
        if result.rowcount == 0:
            # A previous attempt may have committed before its response was lost.
            if record["display_name"] != update.display_name or record["invoice_email"] != update.invoice_email:
                raise HTTPException(status_code=409, detail="Customer changed since the version you supplied.")
        return record
