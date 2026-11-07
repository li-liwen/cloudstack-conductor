"""CloudStack SAML provisioner tests with respx (no network)."""

from __future__ import annotations

import httpx
import pytest
import respx

from conductor.identity.errors import SamlAuthorizationFailed
from conductor.identity.provisioner import CloudStackSamlProvisioner

API = "https://cloud.example.com/client/api"
USER_ID = "11111111-1111-4111-8111-111111111111"
ENTITY_ID = "https://auth.example.com/application/saml/cloudstack-domain-one/metadata/"


@pytest.mark.asyncio
@respx.mock
async def test_authorize_saml_sso_calls_the_cloudstack_command(cloudstack_credentials) -> None:
    route = respx.get(API).mock(return_value=httpx.Response(200, json={"successresponse": {"success": True}}))
    provisioner = CloudStackSamlProvisioner(cloudstack_credentials)
    assert await provisioner.authorize_saml_sso(user_id=USER_ID, entity_id=ENTITY_ID) is True
    request = route.calls[0].request
    params = request.url.params
    assert params["command"] == "authorizeSamlSso"
    assert params["userid"] == USER_ID
    assert params["entityid"] == ENTITY_ID
    assert params["enable"] == "true"
    assert params["apiKey"] == "synthetic-provisioner-key"
    assert params["signature"]
    await provisioner.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_false_success_response_is_a_failure(cloudstack_credentials) -> None:
    respx.get(API).mock(return_value=httpx.Response(200, json={"successresponse": {"success": False}}))
    provisioner = CloudStackSamlProvisioner(cloudstack_credentials)
    with pytest.raises(SamlAuthorizationFailed):
        await provisioner.authorize_saml_sso(user_id=USER_ID, entity_id=ENTITY_ID)
    await provisioner.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_cloudstack_error_does_not_echo_upstream_text(cloudstack_credentials) -> None:
    respx.get(API).mock(
        return_value=httpx.Response(
            401,
            json={"errorresponse": {"errorcode": 531, "errortext": "synthetic upstream detail"}},
        )
    )
    provisioner = CloudStackSamlProvisioner(cloudstack_credentials)
    with pytest.raises(SamlAuthorizationFailed) as error:
        await provisioner.authorize_saml_sso(user_id=USER_ID, entity_id=ENTITY_ID)
    assert "synthetic upstream detail" not in str(error.value)
    await provisioner.aclose()
