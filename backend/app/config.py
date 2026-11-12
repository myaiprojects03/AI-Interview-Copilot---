"""Central settings. All secrets come from environment variables, never from code."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Reads the project-root .env no matter which folder the server is started from.
    # Real environment variables (e.g. in Docker/production) take priority over the file.
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    app_env: str = "development"
    frontend_origin: str = "http://localhost:3000"
    # Hosts that cut long connections (Vercel closes them at 300 s): after this many seconds the
    # server asks the browser, at a quiet moment, to switch to a fresh connection. 0 = never.
    session_rotate_s: int = 0

    # Provider selection (swappable by config)
    stt_provider: str = "assemblyai"    # or "deepgram" (both: free signup credit)
    llm_provider: str = "groq"          # or "openai_compatible" (OpenRouter, Ollama, OpenAI...)
    embedding_provider: str = "local"   # fastembed, runs in-process, no API cost
    vector_provider: str = "pinecone"   # free Starter plan

    # Provider credentials / models
    assemblyai_api_key: str = ""
    assemblyai_speech_model: str = "universal-streaming-english"
    deepgram_api_key: str = ""
    deepgram_model: str = "nova-3"
    groq_api_key: str = ""
    groq_answer_model: str = "openai/gpt-oss-120b"
    groq_fast_model: str = "openai/gpt-oss-20b"  # question detection / classification
    # gpt-oss models reason before answering; "low" keeps time-to-first-word short. Empty = not sent.
    groq_reasoning_effort: str = "low"
    # LLM_PROVIDER=openai_compatible: any OpenAI-compatible Chat Completions API
    llm_base_url: str = ""              # e.g. https://openrouter.ai/api/v1 or http://localhost:11434/v1
    llm_api_key: str = ""               # empty for a local Ollama
    llm_answer_model: str = ""
    llm_fast_model: str = ""            # question detection; empty = same as the answer model
    llm_reasoning_effort: str = ""      # only for models that support it
    llm_json_mode: bool = True          # turn off for models without response_format=json_object
    llm_service_name: str = ""          # shown in error messages, e.g. "OpenRouter"
    llm_timeout_s: float = 30.0         # raise for a slow local model
    local_embedding_model: str = "BAAI/bge-small-en-v1.5"  # 384 dims
    pinecone_api_key: str = ""
    pinecone_index: str = "interview-copilot"
    pinecone_cloud: str = "aws"          # the free Starter plan only allows aws / us-east-1
    pinecone_region: str = "us-east-1"

    # Infrastructure
    database_url: str = "postgresql://copilot:copilot@localhost:5432/copilot"
    redis_url: str = "redis://localhost:6379/0"

    # Auth: the backend checks passwords and issues signed access tokens; Auth.js (frontend) keeps
    # the token in its session cookie and sends it with every request.
    jwt_secret: str = ""            # required outside development
    access_token_days: int = 7
    internal_api_secret: str = ""   # shared with the Next.js server for the Google sign-in hand-off
    admin_emails: str = ""          # comma-separated: these accounts can see the latency report

    # Payments (Stripe). Without a secret key the app runs and paid plans show as not yet available.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""     # signing secret of the webhook endpoint (whsec_...)
    stripe_price_pro: str = ""          # Price IDs (price_...) of the monthly subscriptions
    stripe_price_premium: str = ""
    stripe_api_base: str = ""           # only for local testing against stripe-mock


def is_admin(email: str | None) -> bool:
    admins = {e.strip().lower() for e in get_settings().admin_emails.split(",") if e.strip()}
    return email is not None and email.strip().lower() in admins


@lru_cache
def get_settings() -> Settings:
    return Settings()
