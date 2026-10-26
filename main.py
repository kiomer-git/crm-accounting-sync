from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(title="Mock CRM")

# Temporary example data. We'll add persistent storage in a later step.
customers = [
    {
        "id": "crm-001",
        "company_name": "Acme Rentals",
        "billing_email": "billing@acme.example",
    },
    {
        "id": "crm-002",
        "company_name": "Northstar Events",
        "billing_email": "accounts@northstar.example",
    },
]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/customers")
def list_customers():
    return customers


class BillingUpdate(BaseModel):
    billing_email: str = Field(min_length=1)


@app.patch("/customers/{customer_id}")
def update_billing_email(customer_id: str, update: BillingUpdate):
    for customer in customers:
        if customer["id"] == customer_id:
            customer["billing_email"] = update.billing_email
            return customer
    raise HTTPException(status_code=404, detail="Customer not found.")
