import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from genesis.api.routes import router
from genesis.domain.config import FIXTURES
from genesis.persistence.sqlite import SqliteStore, StoreError
from genesis.runtime.runner import SimulationRunner


def create_app(
    database: Path | None = None,
    *,
    dev_fixtures: bool | None = None,
    delay_scale: float = 1.0,  # tests shorten recorded response delays
) -> FastAPI:
    default = Path(__file__).resolve().parents[3] / "data" / "genesis.sqlite3"
    path = database if database is not None else Path(os.environ.get("GENESIS_DB", default))
    if dev_fixtures is None:
        dev_fixtures = os.environ.get("GENESIS_DEV") == "1"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.dev_fixtures = tuple(sorted(FIXTURES)) if dev_fixtures else ()
        app.state.database = path.resolve()
        app.state.runner = None
        store = SqliteStore(path.resolve())
        try:
            store.initialize()
            app.state.runner = SimulationRunner(store, delay_scale=delay_scale)
        except StoreError as exc:
            # Safe mode (M15): serve a clear technical error instead of failing to start;
            # nothing is repaired, replaced or rewritten.
            logging.getLogger("genesis").error("Storage failed verification: %s", exc)
            app.state.storage_error = str(exc)
            yield
            return
        app.state.runner.start_thread()
        try:
            yield
        finally:
            app.state.runner.stop()

    app = FastAPI(title="Genesis observer", version="0.7.0", lifespan=lifespan)
    app.include_router(router)

    @app.exception_handler(StoreError)
    @app.exception_handler(sqlite3.Error)
    async def storage_error(request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger("genesis").error("Run storage unavailable: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "Run storage unavailable. Existing data has been preserved."},
        )

    @app.middleware("http")
    async def no_stale_state(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


app = create_app()
