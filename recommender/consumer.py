import asyncio
import logging
import os

import aio_pika
from psycopg_pool import ConnectionPool

from producer.publish import QUEUE
from .storage import DATABASE_URL, MODEL, ingest, initialize


async def deliver(message, persist):
    try:
        await asyncio.to_thread(persist, message.body, message.message_id)
    except Exception:
        logging.exception("Could not persist message %s", message.message_id)
        await asyncio.sleep(1)
        await message.nack(requeue=True)
    else:
        await message.ack()


async def main():
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(MODEL, device="cpu")
    def encode(text):
        return model.encode(text, normalize_embeddings=True, show_progress_bar=False)

    with ConnectionPool(DATABASE_URL, min_size=1, max_size=1) as pool:
        with pool.connection() as conn:
            initialize(conn)

        def persist(body, message_id):
            with pool.connection() as conn:
                ingest(conn, body, message_id, encode)

        port = os.getenv("RABBITMQ_PORT", "5672")
        url = os.getenv("RABBITMQ_URL", f"amqp://guest:guest@localhost:{port}/")
        async with await aio_pika.connect_robust(url) as broker:
            channel = await broker.channel()
            await channel.set_qos(prefetch_count=8)
            queue = await channel.declare_queue(QUEUE, durable=True)
            logging.info("Listening on %s", QUEUE)
            async with queue.iterator() as messages:
                async for message in messages:
                    await deliver(message, persist)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
