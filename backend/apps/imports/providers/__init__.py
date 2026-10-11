"""Provider import contract (issue #66, ADR 0011): the types and interface every company data
provider adapter implements. Pure Python: no models, no network, no vendor SDK. Importing this
package registers nothing.

See ``backend/README.md`` ("Provider import contract") for the walk-through.
"""

from .base import (
    ALLOWED_TERMS,
    CompanySearchProvider,
    FilterSupport,
    ProviderCapabilities,
    execute_search,
)
from .errors import (
    ProviderAuthError,
    ProviderBadRequest,
    ProviderError,
    ProviderQuotaExceeded,
    ProviderRateLimited,
    ProviderRegistryError,
    ProviderUnavailable,
)
from .fake import FakeCompanyProvider
from .keys import provider_company_key, split_provider_company_key, try_provider_company_key
from .registry import (
    ProviderRegistry,
    default_registry,
    get_provider,
    list_providers,
    register_provider,
)
from .types import CompanySearchRequest, CompanySearchResult, ProviderCompany

__all__ = [
    "ALLOWED_TERMS",
    "CompanySearchProvider",
    "CompanySearchRequest",
    "CompanySearchResult",
    "FakeCompanyProvider",
    "FilterSupport",
    "ProviderAuthError",
    "ProviderBadRequest",
    "ProviderCapabilities",
    "ProviderCompany",
    "ProviderError",
    "ProviderQuotaExceeded",
    "ProviderRateLimited",
    "ProviderRegistry",
    "ProviderRegistryError",
    "ProviderUnavailable",
    "default_registry",
    "execute_search",
    "get_provider",
    "list_providers",
    "provider_company_key",
    "register_provider",
    "split_provider_company_key",
    "try_provider_company_key",
]
