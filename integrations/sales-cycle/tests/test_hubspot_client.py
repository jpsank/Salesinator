import httpx
import pytest
import respx

from sales_cycle.hubspot_client import HubSpotClient, HubSpotError


@respx.mock
def test_find_by_name_hit():
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": [
            {"id": "123", "properties": {"name": "Acme Corp", "domain": "acme.example.com"}},
        ]})
    )
    client = HubSpotClient(token="tok")
    company = client.find_by_name("Acme")
    assert company is not None
    assert company.id == "123"
    assert company.name == "Acme Corp"
    assert company.domain == "acme.example.com"


@respx.mock
def test_find_by_name_miss():
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    client = HubSpotClient(token="tok")
    assert client.find_by_name("Nonexistent") is None


def test_no_token_raises():
    client = HubSpotClient(token="")
    with pytest.raises(HubSpotError):
        client.find_by_name("Acme")


@respx.mock
def test_http_error_raises_hubspot_error():
    respx.post("https://api.hubapi.com/crm/v3/objects/companies/search").mock(
        return_value=httpx.Response(401, json={"message": "invalid token"})
    )
    client = HubSpotClient(token="bad-token")
    with pytest.raises(HubSpotError):
        client.find_by_name("Acme")
