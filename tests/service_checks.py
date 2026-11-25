"""Exercise HTTP routing and delivery decisions with controlled database outcomes."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from psycopg import OperationalError

from recommender.api import app
from recommender.consumer import deliver


class ApiChecks(unittest.TestCase):
    def setUp(self):
        self.pool = MagicMock()
        self.state_patch = patch.object(app.state, "pool", self.pool, create=True)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.conn = self.pool.connection.return_value.__enter__.return_value
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_company_with_no_description(self):
        self.conn.execute.return_value.fetchall.return_value = [(False, None, None, None)]
        response = self.client.get("/companies/14/similar")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"registration_number": "SYN-000014", "similar": [],
                                           "reason": "no_description"})

    def test_normalized_identifier_and_card(self):
        fields = {"company_name": "Example", "status": "struck_off"}
        self.conn.execute.return_value.fetchall.return_value = [(True, "SYN-000186", fields, 0.25)]
        response = self.client.get("/companies/%20syn-000297%20/similar?limit=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.conn.execute.call_args.args[1], (1, "SYN-000297"))
        self.assertEqual(response.json()["similar"], [{"registration_number": "SYN-000186",
                         "company_name": "Example", "status": "struck_off", "industry": None,
                         "state": None, "similarity": 0.75}])

    def test_missing_company(self):
        self.conn.execute.return_value.fetchall.return_value = []
        for reg in ("SYN-999999", "invalid"):
            with self.subTest(reg=reg):
                response = self.client.get(f"/companies/{reg}/similar")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["error"]["code"], "company_not_found")

    def test_invalid_limits(self):
        for limit in ("0", "51", "abc"):
            with self.subTest(limit=limit):
                response = self.client.get(f"/companies/1/similar?limit={limit}")
                self.assertEqual(response.status_code, 422)
        self.pool.connection.assert_not_called()

    def test_unavailable_database(self):
        self.pool.connection.side_effect = OperationalError("simulated outage")
        response = self.client.get("/companies/1/similar")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "database_unavailable")


class DeliveryChecks(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.message = AsyncMock(body=b"example", message_id="m")

    async def test_ack_follows_persistence(self):
        def persist(body, message_id):
            self.assertEqual((body, message_id), (b"example", "m"))
            self.message.ack.assert_not_awaited()

        await deliver(self.message, persist)
        self.message.ack.assert_awaited_once_with()
        self.message.nack.assert_not_awaited()

    async def test_database_error_requeues_delivery(self):
        persist = MagicMock(side_effect=OperationalError("simulated outage"))
        with patch("recommender.consumer.asyncio.sleep", new_callable=AsyncMock), self.assertLogs(level="ERROR"):
            await deliver(self.message, persist)
        self.message.nack.assert_awaited_once_with(requeue=True)
        self.message.ack.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
