from __future__ import annotations

import json
import os
import sys
from typing import Any, cast

ADC_SOURCE_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def resolve_function_tool() -> Any:
    if "pytest" in sys.modules:
        def passthrough(*decorator_args: Any, **decorator_kwargs: Any) -> Any:
            if decorator_args and callable(decorator_args[0]) and len(decorator_args) == 1 and not decorator_kwargs:
                return decorator_args[0]

            def decorator(func: Any) -> Any:
                return func

            return decorator

        return passthrough

    import agents

    return cast(Any, agents).function_tool


def resolve_delegated_google_user() -> str | None:
    return os.getenv("GOOGLE_DELEGATED_USER") or os.getenv("GMAIL_DELEGATED_USER")


def resolve_google_service_account_email(credentials: Any | None = None) -> str | None:
    email = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL")
    if email:
        return email

    service_account_email = getattr(credentials, "service_account_email", None)
    if service_account_email and service_account_email != "default":
        return str(service_account_email)

    signer_email = getattr(credentials, "signer_email", None)
    if signer_email:
        return str(signer_email)

    return None


def load_google_credentials(
    *,
    scopes: list[str],
    delegated_user: str | None = None,
    missing_credentials_message: str,
) -> Any:
    credentials_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    credentials_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

    import google.oauth2.service_account as service_account_module

    credentials_cls = cast(Any, service_account_module).Credentials

    if credentials_json:
        credentials = credentials_cls.from_service_account_info(json.loads(credentials_json), scopes=scopes)
    elif credentials_file:
        credentials = credentials_cls.from_service_account_file(credentials_file, scopes=scopes)
        return credentials.with_subject(delegated_user) if delegated_user else credentials

    import google.auth as google_auth_module

    source_scopes = ADC_SOURCE_SCOPES if delegated_user else scopes
    default = cast(Any, google_auth_module).default

    try:
        credentials, _project_id = default(scopes=source_scopes)
    except Exception as exc:
        raise RuntimeError(missing_credentials_message) from exc

    if not delegated_user:
        return credentials

    target_principal = resolve_google_service_account_email(credentials)
    if not target_principal:
        raise RuntimeError(
            "Delegated Google API access requires a service account identity. "
            "Set GOOGLE_SERVICE_ACCOUNT_EMAIL when using Application Default Credentials if it cannot be detected automatically."
        )

    import google.auth.impersonated_credentials as impersonated_credentials_module

    impersonated_credentials_cls = cast(Any, impersonated_credentials_module).Credentials
    return impersonated_credentials_cls(
        source_credentials=credentials,
        target_principal=target_principal,
        target_scopes=scopes,
        subject=delegated_user,
    )


def load_service_account_credentials(
    *,
    scopes: list[str],
    delegated_user: str | None = None,
    missing_credentials_message: str,
) -> Any:
    return load_google_credentials(
        scopes=scopes,
        delegated_user=delegated_user,
        missing_credentials_message=missing_credentials_message,
    )
