from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    dograh_api_url: str = "http://api:8000"
    dograh_internal_token: str = ""
    google_api_key: str = ""
    openai_api_key: str = ""
    deepgram_api_key: str = ""
    cartesia_api_key: str = ""
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
