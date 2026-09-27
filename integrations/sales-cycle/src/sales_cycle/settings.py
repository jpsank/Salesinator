"""Config for the sales-cycle integration — a validated contract delivered by env (P14 in spirit;
this package lives outside core/ so it isn't gated by config.v1, but follows the same discipline)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SALES_CYCLE_")

    # Vexa's own gateway — the only way this package talks to Vexa (external HTTP client, no in-process
    # imports of core.meetings internals).
    vexa_gateway_url: str = "http://gateway:8000"

    # HubSpot private-app token (plain HTTP client, no SDK — Category-A licensing, ADR-0004).
    hubspot_token: str = ""
    hubspot_base_url: str = "https://api.hubapi.com"

    # The workspace slug a meeting falls back to when no customer_tag was given and no calendar match
    # resolved one. Never blocks the meeting — just means a human triages it later.
    unmapped_workspace_slug: str = "unmapped"


def get_settings() -> Settings:
    return Settings()
