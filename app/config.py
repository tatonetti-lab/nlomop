from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AzureOpenAIConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NLOMOP_AZURE_OPENAI__",
        env_file=".env",
        extra="ignore",
    )

    endpoint: str
    api_key: str
    deployment: str = "gpt-4o-mini"
    api_version: str = "2024-12-01-preview"


class DatabaseConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NLOMOP_DB__",
        env_file=".env",
        extra="ignore",
    )

    host: str = "localhost"
    port: int = 5432
    name: str = "synthea10"
    user: str = "TatonettiN"
    password: str = ""
    schema_: str = Field("cdm_synthea", alias="NLOMOP_DB__SCHEMA", validation_alias="NLOMOP_DB__SCHEMA")
    query_timeout_s: int = 30

    @property
    def conninfo(self) -> str:
        parts = f"host={self.host} port={self.port} dbname={self.name} user={self.user}"
        if self.password:
            parts += f" password={self.password}"
        return parts


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    azure_openai: AzureOpenAIConfig = AzureOpenAIConfig()  # type: ignore[call-arg]
    db: DatabaseConfig = DatabaseConfig()  # type: ignore[call-arg]


settings = Settings()
