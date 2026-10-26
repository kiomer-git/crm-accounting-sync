from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

import sync
import sync_state


class PartialFailureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_patch = patch("sync_state.DB_PATH", Path(temporary.name) / "sync.db")
        state_patch.start()
        self.addCleanup(state_patch.stop)

    def test_failed_customer_does_not_block_the_next_customer(self):
        for failure_status in (409, 503):
            with self.subTest(failure_status=failure_status):
                # Each scenario starts with no existing mappings.
                sync_state.DB_PATH.unlink(missing_ok=True)
                posted_ids = []

                def handle(request):
                    if request.method == "GET":
                        return httpx.Response(200, json=[
                            {"id": crm_id, "company_name": "Example", "billing_email": "a@example.test"}
                            for crm_id in ("crm-001", "crm-002")
                        ])
                    import json
                    payload = json.loads(request.content)
                    posted_ids.append(payload["crm_id"])
                    if payload["crm_id"] == "crm-001":
                        return httpx.Response(failure_status, json={"detail": "Simulated failure"})
                    return httpx.Response(201, json={"id": "acct-002", "version": 1})

                output = io.StringIO()
                client = httpx.Client(transport=httpx.MockTransport(handle))
                with patch("sync.httpx.Client", return_value=client), patch("sync.sleep"), redirect_stdout(output):
                    exit_code = sync.main()

                attempts = 3 if failure_status == 503 else 1
                self.assertEqual(posted_ids, ["crm-001"] * attempts + ["crm-002"])
                self.assertEqual(exit_code, 1)
                self.assertIn("Finished: 1 succeeded, 1 failed.", output.getvalue())


class RetryTests(unittest.TestCase):
    def test_temporary_failures_recover_with_exponential_backoff(self):
        statuses = iter([503, 503, 201])
        def handle(request):
            return httpx.Response(next(statuses), json={"id": "acct-test"})
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with patch("sync.sleep") as wait, redirect_stdout(io.StringIO()):
                response = sync.create_with_retries(client, {"crm_id": "crm-001"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual([call.args[0] for call in wait.call_args_list], [0.5, 1.0])

    def test_network_timeout_is_retried(self):
        requests = []
        def handle(request):
            requests.append(request)
            if len(requests) == 1:
                raise httpx.ReadTimeout("Simulated timeout", request=request)
            return httpx.Response(200, json={"id": "acct-existing"})
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            with patch("sync.sleep"), redirect_stdout(io.StringIO()):
                response = sync.create_with_retries(client, {"crm_id": "crm-001"})
        self.assertEqual(len(requests), 2)
        self.assertEqual(response.status_code, 200)
