from time import sleep

import httpx

import sync_state


CRM_URL = "http://127.0.0.1:8000"
ACCOUNTING_URL = "http://127.0.0.1:8001"
MAX_ATTEMPTS = 3
RETRYABLE_STATUSES = {500, 502, 503, 504}


def request_with_retries(client, method, url, crm_id, **kwargs):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as error:
            if error.response.status_code not in RETRYABLE_STATUSES or attempt == MAX_ATTEMPTS:
                raise
            reason = f"HTTP {error.response.status_code}"
        except httpx.RequestError:
            if attempt == MAX_ATTEMPTS:
                raise
            reason = "network error"

        delay = 0.5 * (2 ** (attempt - 1))
        print(f"Retry: {crm_id} — {reason}; attempt {attempt + 1}/{MAX_ATTEMPTS} in {delay}s.")
        sleep(delay)


def create_with_retries(client, payload):
    return request_with_retries(
        client, "POST", f"{ACCOUNTING_URL}/customers", payload["crm_id"], json=payload
    )


class SyncConflict(Exception):
    pass


def sync_customer(client, payload):
    crm_id = payload["crm_id"]
    previous = sync_state.load(crm_id)
    if previous is None:
        response = create_with_retries(client, payload)
        record = response.json()
        action = "Created" if response.status_code == 201 else "Already exists"
    else:
        url = f"{ACCOUNTING_URL}/customers/{previous['accounting_id']}"
        record = request_with_retries(client, "GET", url, crm_id).json()
        desired = {field: payload[field] for field in ("display_name", "invoice_email")}
        if all(record[field] == value for field, value in desired.items()):
            # Also recovers a write that committed before the sync state was saved.
            action = "Already exists"
        else:
            if record["version"] != previous["version"]:
                raise SyncConflict("Accounting changed independently; needs review.")
            record = request_with_retries(
                client, "PATCH", url, crm_id,
                json={**desired, "expected_version": previous["version"]},
            ).json()
            action = "Updated"

    sync_state.save(crm_id, record)
    return action, record


def main():
    succeeded = 0
    failed = 0
    with httpx.Client(timeout=5.0) as client:
        # Read the source records from the CRM over HTTP.
        response = client.get(f"{CRM_URL}/customers")
        response.raise_for_status()
        customers = response.json()
        print(f"Found {len(customers)} CRM customers.")

        for customer in customers:
            # Translate the CRM's fields into the accounting system's format.
            payload = {
                "crm_id": customer["id"],
                "display_name": customer["company_name"],
                "invoice_email": customer["billing_email"],
            }

            try:
                action, record = sync_customer(client, payload)
            except SyncConflict as error:
                failed += 1
                print(f"Conflict: {customer['id']} — {error}")
                continue
            except httpx.HTTPStatusError as error:
                failed += 1
                if error.response.status_code == 409:
                    print(f"Conflict: {customer['id']} — details differ; needs review.")
                else:
                    print(f"Failed: {customer['id']} — HTTP {error.response.status_code}.")
                continue
            except httpx.RequestError as error:
                failed += 1
                print(f"Failed: {customer['id']} — {error}")
                continue

            print(f"{action}: {customer['id']} -> {record['id']}")
            succeeded += 1

    print(f"Finished: {succeeded} succeeded, {failed} failed.")
    # A nonzero exit code tells a caller that the run needs attention.
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except httpx.HTTPError as error:
        print(f"Copy stopped: {error}")
        raise SystemExit(1)
