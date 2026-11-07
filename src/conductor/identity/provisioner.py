"""CloudStack SAML provisioning adapter.

This is the only place an administrative CloudStack credential is used. It
calls the SAML 2.0 plugin command available as ``authorizeSamlSso`` in
CloudStack 4.22.1.1:

    command=authorizeSamlSso
    userid=<CloudStack user UUID>
    entityid=<authentik SAML provider entity ID>
    enable=true

The command is a synchronous admin API (it returns a ``successresponse``, not
an async job). It stores the IdP entity ID on the CloudStack user and marks the
source as SAML. A customer request never receives this credential.
"""

from __future__ import annotations

import logging

import httpx

from conductor.cloudstack.client import CloudStackClient, CloudStackCredentials
from conductor.errors import CloudStackError
from conductor.identity.errors import SamlAuthorizationFailed

logger = logging.getLogger(__name__)


class CloudStackSamlProvisioner:
    def __init__(
        self,
        credentials: CloudStackCredentials,
        *,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = CloudStackClient(
            credentials,
            timeout_seconds=timeout_seconds,
            max_concurrent=1,
            rate_per_second=1.0,
            transport=transport,
        )

    async def authorize_saml_sso(
        self,
        *,
        user_id: str,
        entity_id: str,
        enable: bool = True,
    ) -> bool:
        if not user_id or not entity_id:
            raise SamlAuthorizationFailed("the SAML authorization parameters are incomplete")
        try:
            body = await self._client.call_mutation_and_wait(
                "authorizeSamlSso",
                timeout_seconds=30.0,
                userid=user_id,
                entityid=entity_id,
                enable="true" if enable else "false",
            )
        except CloudStackError as exc:
            logger.warning("CloudStack SAML authorization failed: code=%s", exc.error_code)
            raise SamlAuthorizationFailed() from exc
        success = body.get("success")
        if isinstance(success, str):
            accepted = success.strip().lower() == "true"
        else:
            accepted = bool(success)
        if not accepted:
            raise SamlAuthorizationFailed()
        return True

    async def aclose(self) -> None:
        await self._client.close()
