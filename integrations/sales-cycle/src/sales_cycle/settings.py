"""Every setting this add-on reads from the environment, in one place, with what each one is for.
Set them as env vars prefixed `SALES_CYCLE_` (e.g. `SALES_CYCLE_HUBSPOT_TOKEN`)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SALES_CYCLE_")

    # Vexa's own web address — this is the only way this add-on talks to Vexa.
    vexa_gateway_url: str = "http://gateway:8000"

    # HubSpot's login token, so we can look up companies. Plain web requests, no HubSpot software
    # library installed — keeps this add-on's dependencies simple and license-clean.
    hubspot_token: str = ""
    hubspot_base_url: str = "https://api.hubapi.com"

    # If a call never gets tagged with a customer (no Slack tag, no calendar match), its notes land
    # here instead — nothing is ever lost, it just needs a human to sort it out later.
    unmapped_workspace_slug: str = "unmapped"

    # Slack login token, signing secret (to prove a request is really from Slack), and which channel
    # feature-request messages get posted to.
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_channel_id: str = ""

    # Where Vexa's customer folders live on disk — this add-on reads meeting notes directly from here.
    workspaces_root: str = "/workspaces"

    # Where this add-on keeps its own small local database (which requests are pending/approved/done).
    db_path: str = "/data/sales-cycle.db"

    # The address for Vexa's AI-agent service, used directly rather than through Vexa's usual front
    # door. Why: we tested it, and starting an AI agent through the usual front door doesn't work on
    # this setup yet (it answers "not found" — likely just missing from that front door's route list).
    # Going straight to the AI-agent service works fine, and it's not reachable from outside anyway
    # (only from other services running alongside it), so this isn't a workaround that weakens
    # security — it's just skipping a front door that has a gap in it right now.
    agent_api_internal_url: str = "http://agent-api:8100"

    # Which Vexa account "owns" the actual product codebase. Set up once, by hand: create an account,
    # point its files at your real GitHub repo, and put its account number here. Every "build this
    # feature" request runs as that account, so it's always working in the right codebase.
    product_repo_user_id: str = ""

    # For auto-detecting the customer from calendar invites: the secret that proves a "meeting started"
    # alert really came from Vexa, and the login key used to tag the meeting once we've figured out
    # which customer it's for. (v1 supports one rep's calendar; supporting many reps at once is a
    # straightforward later extension, not needed for a first setup.)
    calendar_webhook_secret: str = ""
    calendar_api_key: str = ""
    # Your own company's email domain(s), comma-separated — so we don't try to look up your own
    # teammates in HubSpot as if they were the customer. Whatever's left over is the customer's domain.
    own_domains: str = ""


def get_settings() -> Settings:
    return Settings()
