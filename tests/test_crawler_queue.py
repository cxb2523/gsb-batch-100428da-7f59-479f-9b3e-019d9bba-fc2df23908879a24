import asyncio
import unittest

from core.crawler import run_queue_workers


class TestQueueWorkers(unittest.TestCase):
    def test_join_returns_when_limit_reached_with_items_left(self):
        """Items abandoned after the page limit must still be task_done()'d."""
        async def scenario():
            q = asyncio.Queue()
            for i in range(10):
                q.put_nowait(i)
            seen = []

            async def handle(item):
                seen.append(item)

            await asyncio.wait_for(
                run_queue_workers(q, 2, handle, lambda: len(seen) < 3), timeout=5)
            return seen

        seen = asyncio.run(scenario())
        self.assertEqual(len(seen), 3)

    def test_worker_does_not_exit_on_transient_empty_queue(self):
        """A momentarily empty queue must not end the crawl while peers enqueue."""
        async def scenario():
            q = asyncio.Queue()
            q.put_nowait(0)
            seen = []

            async def handle(item):
                seen.append(item)
                if item < 5:
                    await asyncio.sleep(0)
                    q.put_nowait(item + 1)

            await asyncio.wait_for(
                run_queue_workers(q, 4, handle, lambda: True), timeout=5)
            return seen

        seen = asyncio.run(scenario())
        self.assertEqual(sorted(seen), [0, 1, 2, 3, 4, 5])


if __name__ == "__main__":
    unittest.main()
