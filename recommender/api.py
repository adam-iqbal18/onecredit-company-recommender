from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from psycopg import OperationalError
from psycopg_pool import ConnectionPool, PoolTimeout

from .events import registration
from .storage import DATABASE_URL, initialize, recommendations


@asynccontextmanager
async def lifespan(app):
    with ConnectionPool(DATABASE_URL, min_size=1, max_size=4, timeout=3) as pool:
        with pool.connection() as conn:
            initialize(conn)
        app.state.pool = pool
        yield


app = FastAPI(lifespan=lifespan)


def error(status, code, message):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.exception_handler(PoolTimeout)
@app.exception_handler(OperationalError)
async def unavailable(request, exc):
    return error(503, "database_unavailable", "The database is unavailable.")


@app.get("/companies/{registration_number}/similar")
def similar(registration_number: str, request: Request, limit: int = Query(10, ge=1, le=50)):
    try:
        reg = registration(registration_number)
    except ValueError:
        return error(404, "company_not_found", "Unknown company.")
    with request.app.state.pool.connection() as conn:
        result = recommendations(conn, reg, limit)
    if result is None:
        return error(404, "company_not_found", "Unknown company.")
    return result
