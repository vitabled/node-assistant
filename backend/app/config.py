from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    encryption_key: str = "dev_key_change_in_production_000"
    max_ssh_sessions: int = 5
    log_buffer_size: int = 2000
    cors_origin: str = "http://localhost"

    class Config:
        env_file = ".env"


settings = Settings()
