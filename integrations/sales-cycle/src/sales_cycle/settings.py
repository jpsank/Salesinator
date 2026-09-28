"""Every setting this add-on reads from the environment, in one place, with what each one is for.
Set them as env vars prefixed `SALES_CYCLE_` (e.g. `SALES_CYCLE_HUBSPOT_TOKEN`)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SALES_CYCLE_")

    # Vexa's own web address — this is the only way this add-on talks to Vexa.
    vexa_gateway_url: str = "http://gateway:8000"

    # HubSpot's login token, so we can look up companies. Plain web requests, no HubSpot software
    # library installed — keeps this add-on's dependencies simple and license-clean.
    #
    # Two ways to authenticate, pick one: a static private-app token (hubspot_token, set once by
    # hand — simplest, fine for one operator's own testing), or the OAuth connection below (what
    # powers the "Connect HubSpot" button in Vexa's Settings page — the one an actual sales team
    # uses, since it's a real click-through consent flow instead of copy-pasting a token).
    hubspot_token: str = ""
    hubspot_base_url: str = "https://api.hubapi.com"

    # The OAuth app's OWN identity — registered once by whoever operates this deployment, at
    # HubSpot's developer portal (developers.hubspot.com → an app with the OAuth product, not a
    # private app). Not a per-user secret; every rep's "Connect HubSpot" click goes through this
    # one app. redirect_uri must exactly match what's registered there.
    hubspot_oauth_client_id: str = ""
    hubspot_oauth_client_secret: str = ""
    hubspot_oauth_redirect_uri: str = ""
    hubspot_oauth_scopes: str = "crm.objects.companies.read"

    # Where to send the browser back to once a "Connect X" flow finishes — Vexa's own Terminal UI.
    terminal_url: str = "http://localhost:13000"

    # If a call never gets tagged with a customer (no Slack tag, no calendar match), its notes land
    # here instead — nothing is ever lost, it just needs a human to sort it out later.
    unmapped_workspace_slug: str = "unmapped"

    # Slack login token (static, or via OAuth below — same pick-one as HubSpot), a signing secret
    # (to prove an Events API request is really from Slack — a SEPARATE credential from the OAuth
    # ones below; it authenticates INCOMING calls from Slack, not our outgoing ones), and which
    # channel feature-request messages get posted to.
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_channel_id: str = ""

    # The OAuth app's OWN identity — registered once at api.slack.com/apps ("Connect Slack" in
    # Vexa's Settings page). Standard (non-rotating) Slack bot tokens don't expire, so unlike
    # HubSpot there's no refresh step — just the one-time exchange.
    slack_oauth_client_id: str = ""
    slack_oauth_client_secret: str = ""
    slack_oauth_redirect_uri: str = ""
    slack_oauth_scopes: str = "chat:write,channels:read,groups:read"

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
