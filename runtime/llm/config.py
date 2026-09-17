from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    api_base_url: str
    api_key: str
    model: str
    timeout: float = 60.0

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="LLM_",
        extra="ignore",
    )
