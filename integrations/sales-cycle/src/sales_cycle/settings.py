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

    # If a call never gets tagged with a customer, bind it to this Vexa workspace instead of leaving
    # it unbound forever. Empty (default) keeps today's behavior: an unresolved call's workspace_id
    # stays blank, and the live meeting copilot falls back to its own generic placeholder workspace
    # (which may not exist on disk — see core/agent's transcription_watcher.py). Set it to a real,
    # already-seeded subject (e.g. your product_repo_subject) to give every untagged call a safe home.
    fallback_workspace_id: str = ""

    # The moment our bot joins a call, turn on Vexa's live meeting copilot for it — inviting the bot
    # IS the consent signal, so no separate rep action is required. Off restores today's behavior: a
    # rep must open the call in Vexa's Terminal for the copilot to start watching it.
    auto_process_calls: bool = True

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
    # reactions:read is REQUIRED for the reaction_added event (the ✓-approval flow) to be delivered
    # at all — Slack silently drops an event subscription the bot token doesn't hold the scope for.
    slack_oauth_scopes: str = "chat:write,channels:read,groups:read,reactions:read"

    # Where this add-on keeps its own small local database (which requests are pending/approved/done).
    db_path: str = "/data/sales-cycle.db"

    # The address for Vexa's AI-agent service, used directly rather than through Vexa's usual front
    # door. Why: we tested it, and starting an AI agent through the usual front door doesn't work on
    # this setup yet (it answers "not found" — likely just missing from that front door's route list).
    # Going straight to the AI-agent service works fine, and it's not reachable from outside anyway
    # (only from other services running alongside it), so this isn't a workaround that weakens
    # security — it's just skipping a front door that has a gap in it right now.
    agent_api_internal_url: str = "http://agent-api:8100"

    # Companion to agent_api_internal_url above, for meeting-api. Different reason: the live card
    # watcher (live_card_watcher.py) is launched off a webhook, not a rep's own request, so there's
    # no per-rep API key to hand the gateway — it authenticates as the meeting's own dispatching user
    # straight to these two internal services instead, the same internal server-to-server trust
    # orchestrator.py's submit_implementation/check_and_push already rely on for agent-api. Neither
    # address is reachable from outside the deployment.
    meeting_api_internal_url: str = "http://meeting-api:8080"

    # The Vexa subject whose OWN workspace is the actual product codebase. Set up once: attach the
    # real GitHub repo to this subject's workspace (Settings → Integrations → GitHub's "Product
    # repo" picker — see README.md; it's the same swap primitive any workspace attach uses). A
    # subject is just a slug in this system — it never needs to be a real logged-in human account.
    # Every "build this feature" request runs AS that subject, so it's always working in the right
    # codebase. Defaults to "product-repo", matching agent-api's own workspace_delegate_subject
    # default (core/agent/shared/config.py) — the two must name the SAME subject for the picker's
    # attach step to land where this add-on then looks for it; only change one if you change both.
    product_repo_subject: str = "product-repo"

    # The branch a pushed feature request's pull request opens AGAINST — the product repo's own
    # main line. Change only if the product repo's default branch isn't "main".
    product_repo_default_branch: str = "main"

    # Which agent CLI runner drives the implementation turn (unit.v1's own `runner` field —
    # core/agent/llm/registry.py's HARNESS_RUNNERS). Defaults to "claude-code" (today's exact
    # behavior, no regression). Set to "opencode" to run the feature-implementer against whatever
    # local/open-source model VEXA_LLM_BASE_URL points at instead — see core/agent/llm/opencode.py.
    product_repo_runner: str = "claude-code"

    # Attribution for every automated commit this add-on's AI turns make (core/agent's
    # workspace_worktree.py signoff mechanism — see CONTRIBUTOR_RIGHTS.md for why this exists: the
    # human who ships automated work owns full authorship/responsibility for it). Both empty
    # (default) = no `identity.principal` is sent at all, so commits carry whatever
    # subject@vexa.local fallback identity core/agent already uses on its own — a generic
    # deployment with no real identity configured pays nothing here. Set BOTH together, never one
    # alone, to a real name/email (e.g. whoever is accountable for what this deployment ships) to
    # get correctly-attributed, signed-off commits instead.
    product_repo_signoff_name: str = ""
    product_repo_signoff_email: str = ""

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
