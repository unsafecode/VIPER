from __future__ import annotations

from typing import Optional

from azure.identity import (
    AzureCliCredential,
    AzureDeveloperCliCredential,
    ChainedTokenCredential,
    ManagedIdentityCredential,
)
from azure.identity.aio import (
    AzureCliCredential as AsyncAzureCliCredential,
    AzureDeveloperCliCredential as AsyncAzureDeveloperCliCredential,
    ChainedTokenCredential as AsyncChainedTokenCredential,
    ManagedIdentityCredential as AsyncManagedIdentityCredential,
)


def build_azure_credential(
    *, managed_identity_client_id: Optional[str] = None
) -> ChainedTokenCredential:
    """Build the shared Azure credential chain for local and hosted execution."""

    return ChainedTokenCredential(
        AzureDeveloperCliCredential(),
        AzureCliCredential(),
        ManagedIdentityCredential(client_id=managed_identity_client_id),
    )


def build_async_azure_credential(
    *, managed_identity_client_id: Optional[str] = None
) -> AsyncChainedTokenCredential:
    """Build the shared async Azure credential chain."""

    return AsyncChainedTokenCredential(
        AsyncAzureDeveloperCliCredential(),
        AsyncAzureCliCredential(),
        AsyncManagedIdentityCredential(client_id=managed_identity_client_id),
    )
