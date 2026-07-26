from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    encryption_key: str = "dev_key_change_in_production_000"
    max_ssh_sessions: int = 5
    log_buffer_size: int = 2000
    cors_origin: str = "http://localhost"
    # Shared HMAC secret for the Remnawave webhook receiver (matches Remnawave's
    # own env var name). Empty → the webhook endpoint rejects everything (401),
    # since it can't verify a signature. GLOBAL (one Remnawave → one secret → one
    # webhook URL); rules run across all accounts on a verified event.
    webhook_secret_header: str = ""

    class Config:
        env_file = ".env"
        # ⚠️ pydantic-settings forbids extra keys by default, and it feeds EVERY
        # key of the .env file into the model — so a shared .env (it also carries
        # AGG_TOKEN, TASK_STORE, PROXY_DOMAIN, ACME_EMAIL for compose and the
        # proxy) made Settings() raise `extra_forbidden` for anything not declared
        # here. Containers never hit it (compose injects env vars and no .env is
        # copied into the image), but running the backend from the repo root did.
        extra = "ignore"


settings = Settings()
