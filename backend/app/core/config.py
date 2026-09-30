from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/video_discovery"

    meta_access_token: str = ""
    meta_ad_library_country: str = "IN"
    meta_max_requests_per_search: int = 10

    instagram_provider: str = "rapidapi"
    instagram_api_key: str = ""
    instagram_api_host: str = ""
    # Hard cap on real HTTP calls the Instagram source can make in ONE
    # search, regardless of widening/pagination logic. Default is small on
    # purpose: many RapidAPI free tiers cap at ~20-50 requests/MONTH, so a
    # single search must never be able to exhaust that on its own.
    instagram_max_requests_per_search: int = 3

    tiktok_enabled: bool = False
    tiktok_api_key: str = ""

    vision_provider: str = "local_clip"
    openai_api_key: str = ""

    match_score_threshold: int = 55

    backend_port: int = 8000
    frontend_origin: str = "http://localhost:5173"
    log_level: str = "INFO"


settings = Settings()
