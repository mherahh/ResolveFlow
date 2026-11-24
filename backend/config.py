"""App settings and the LLM factory. Nothing secret is hardcoded: everything comes from .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore")

    llm_provider: str = "openrouter"          # "openrouter" or "openai"
    llm_model: str = "openai/gpt-4o-mini"     # any model name the provider supports
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    database_url: str = "sqlite:///./resolveflow.db"
    frontend_origin: str = "http://localhost:3000"
    demo_mode: bool = True                    # Set DEMO_MODE=false in production
    session_ttl_hours: int = 24
    confirmation_ttl_minutes: int = 15

    @property
    def clean_openrouter_api_key(self) -> str:
        return self.openrouter_api_key.strip()

    @property
    def clean_openai_api_key(self) -> str:
        return self.openai_api_key.strip()


settings = Settings()


def get_llm(temperature: float = 0.0):
    """Return a LangChain chat model for the configured provider."""
    from langchain_openai import ChatOpenAI

    if settings.llm_provider == "openai":
        key = settings.clean_openai_api_key
        if not key:
            raise ValueError("Missing OPENAI_API_KEY. Please set it in backend/.env or run demo_stub_server.py.")
        return ChatOpenAI(model=settings.llm_model, api_key=key, temperature=temperature)
    if settings.llm_provider == "openrouter":
        # OpenRouter exposes an OpenAI-compatible API, so ChatOpenAI works with a different base_url.
        key = settings.clean_openrouter_api_key
        if not key:
            raise ValueError("Missing OPENROUTER_API_KEY. Please set it in backend/.env or run demo_stub_server.py.")
        return ChatOpenAI(
            model=settings.llm_model,
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
            temperature=temperature,
        )
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")
