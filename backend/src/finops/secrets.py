"""Secret storage for values users enter in the UI (currently: Slack webhook URLs).

In AWS, secrets go to SSM Parameter Store as SecureString (KMS-encrypted); only the parameter
*name* is stored in DynamoDB and the value is never returned by the API. In demo mode an
in-memory store is used.
"""

import contextlib
from typing import Any, Protocol


class SecretStore(Protocol):
    def put(self, name: str, value: str) -> None: ...
    def get(self, name: str) -> str | None: ...
    def delete(self, name: str) -> None: ...


class InMemorySecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def put(self, name: str, value: str) -> None:
        self._values[name] = value

    def get(self, name: str) -> str | None:
        return self._values.get(name)

    def delete(self, name: str) -> None:
        self._values.pop(name, None)


class SsmSecretStore:
    def __init__(self, client: Any) -> None:
        self._ssm = client

    def put(self, name: str, value: str) -> None:
        self._ssm.put_parameter(Name=name, Value=value, Type="SecureString", Overwrite=True)

    def get(self, name: str) -> str | None:
        try:
            return self._ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
        except self._ssm.exceptions.ParameterNotFound:
            return None

    def delete(self, name: str) -> None:
        with contextlib.suppress(self._ssm.exceptions.ParameterNotFound):
            self._ssm.delete_parameter(Name=name)


def slack_param_name(prefix: str, account_id: str) -> str:
    return f"{prefix.rstrip('/')}/{account_id}"
