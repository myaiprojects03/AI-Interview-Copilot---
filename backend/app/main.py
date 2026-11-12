import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth_routes import router as auth_router
from app.api.billing_routes import router as billing_router
from app.api.documents import router as documents_router
from app.api.history_routes import router as history_router
from app.api.session_ws import router as session_router
from app.auth import jwt_secret
from app.background import spawn
from app.billing.gateway import build_gateway
from app.billing.service import BillingService
from app.config import get_settings
from app.db.migrations import connect as connect_db
from app.documents.repo import PostgresDocumentRepo
from app.history.repo import PostgresHistoryRepo
from app.providers import load_providers
from app.providers.registry import get_embedder, get_vector_store
from app.retrieval.service import RetrievalService
from app.shared.store import build_store
from app.users.repo import PostgresUserRepo

load_providers()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)

settings = get_settings()


def build_retrieval() -> RetrievalService | None:
    try:
        return RetrievalService(get_embedder(settings), get_vector_store(settings))
    except Exception as exc:
        log.warning("retrieval disabled (%s): answers will use the full CV text", exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    jwt_secret()  # fail fast outside development when JWT_SECRET is missing
    app.state.store = await build_store(settings)  # Redis; required outside development
    # A database outage is logged instead of stopping the server, so /health can report it.
    pool = None
    try:
        pool = await connect_db(settings.database_url)
        app.state.repo = PostgresDocumentRepo(pool)
        app.state.users = PostgresUserRepo(pool)
        app.state.history = PostgresHistoryRepo(pool)
        app.state.billing = BillingService(app.state.users, build_gateway(settings), settings)
        log.info("database connected")
        if not app.state.billing.enabled:
            log.info("payments not configured (STRIPE_SECRET_KEY): paid plans can't be bought yet")
    except Exception as exc:
        app.state.repo = app.state.users = app.state.history = app.state.billing = None
        log.error("database unavailable (%s): sign-in and CV/JD storage disabled. Is Docker running?", exc)

    app.state.retrieval = build_retrieval()
    if app.state.retrieval is not None:
        # Load (and on first run download) the embedding model in the background, so the
        # first upload or question does not wait for it.
        spawn(app, app.state.retrieval.embedder.warm_up(), "embedding warm-up")
    yield
    if app.state.retrieval is not None:
        await app.state.retrieval.aclose()
    if pool is not None:
        await pool.close()
    await app.state.store.aclose()


# The interactive API docs are for development only.
_dev = settings.app_env == "development"
app = FastAPI(title="AI Interview Copilot API", lifespan=lifespan,
              docs_url="/docs" if _dev else None, redoc_url=None, openapi_url="/openapi.json" if _dev else None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin.rstrip("/")],  # browsers send the origin without a slash
    allow_credentials=False,  # the API uses bearer tokens, never cookies
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")  # personal data: never cached
    return response

app.include_router(auth_router)
app.include_router(session_router)
app.include_router(documents_router)
app.include_router(history_router)
app.include_router(billing_router)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "env": settings.app_env,
        "database": getattr(app.state, "repo", None) is not None,
        "retrieval": getattr(app.state, "retrieval", None) is not None,
    }
