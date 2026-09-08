from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from app.adapters.base import ProviderInstanceSummary
from app.domain.enums import Architecture


class AwsEc2Client(Protocol):
    def get_paginator(self, operation_name: str) -> Any:
        raise NotImplementedError

    def describe_instance_types(self, **kwargs: object) -> Mapping[str, Any]:
        raise NotImplementedError

    def describe_volumes(self, **kwargs: object) -> Mapping[str, Any]:
        raise NotImplementedError


AwsClientFactory = Callable[..., Any]
GoogleIdTokenProvider = Callable[[str], str]


class AwsAdapterError(RuntimeError):
    error_code = "AWS_API_ERROR"
    public_message = "AWS API request failed."

    def __init__(self, scope_id: str) -> None:
        self.scope_id = scope_id
        super().__init__(self.public_message)


class AwsDependencyError(AwsAdapterError):
    error_code = "AWS_SDK_NOT_INSTALLED"
    public_message = "The AWS SDK dependency is not installed."


class AwsAuthenticationError(AwsAdapterError):
    error_code = "AWS_AUTHENTICATION_FAILED"
    public_message = "AWS authentication failed."


class AwsPermissionError(AwsAdapterError):
    error_code = "AWS_PERMISSION_DENIED"
    public_message = "The broker AWS role cannot access this account or Region."


class AwsAccountMismatchError(AwsPermissionError):
    error_code = "AWS_ACCOUNT_MISMATCH"
    public_message = "The configured AWS role belongs to a different account."


class AwsRegionNotAccessibleError(AwsPermissionError):
    error_code = "AWS_REGION_NOT_ACCESSIBLE"
    public_message = "The AWS Region was not found, enabled, or accessible."


class AwsResourceLookupError(AwsAdapterError):
    error_code = "AWS_RESOURCE_LOOKUP_FAILED"
    public_message = "AWS VM capacity details could not be collected."


class AwsApiUnavailableError(AwsAdapterError):
    error_code = "AWS_API_UNAVAILABLE"
    public_message = "The AWS API is unavailable or rate limited."


def _require_non_empty_string(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty or whitespace")
    if value != value.strip():
        raise ValueError(f"{name} must not have surrounding whitespace")
    return value


def _optional_non_empty_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _chunks(values: tuple[str, ...], size: int) -> Iterable[tuple[str, ...]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _client_error_code(error: Exception) -> str | None:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    details = response.get("Error")
    if not isinstance(details, Mapping):
        return None
    return _optional_non_empty_string(details.get("Code"))


def _client_http_status(error: Exception) -> int | None:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, Mapping):
        return None
    status = metadata.get("HTTPStatusCode")
    return status if isinstance(status, int) else None


_AUTHENTICATION_ERROR_CODES = frozenset(
    {
        "ExpiredToken",
        "ExpiredTokenException",
        "IDPCommunicationError",
        "IDPRejectedClaim",
        "InvalidClientTokenId",
        "InvalidIdentityToken",
        "MalformedPolicyDocument",
        "SignatureDoesNotMatch",
        "UnrecognizedClientException",
    }
)
_PERMISSION_ERROR_CODES = frozenset(
    {
        "AccessDenied",
        "AccessDeniedException",
        "UnauthorizedOperation",
    }
)
_REGION_ERROR_CODES = frozenset(
    {
        "AuthFailure",
        "InvalidEndpoint",
        "InvalidParameterValue",
        "OptInRequired",
    }
)
_UNAVAILABLE_ERROR_CODES = frozenset(
    {
        "InternalError",
        "InternalFailure",
        "PriorRequestNotComplete",
        "RequestLimitExceeded",
        "RequestTimeout",
        "ServiceUnavailable",
        "SlowDown",
        "ThrottledException",
        "Throttling",
        "ThrottlingException",
    }
)


def _translate_aws_error(
    scope_id: str,
    error: Exception,
    *,
    assume_role: bool = False,
) -> AwsAdapterError:
    try:
        from botocore import exceptions as botocore_exceptions
    except ModuleNotFoundError:
        return AwsDependencyError(scope_id)

    if isinstance(
        error,
        (
            botocore_exceptions.CredentialRetrievalError,
            botocore_exceptions.NoCredentialsError,
            botocore_exceptions.PartialCredentialsError,
        ),
    ):
        return AwsAuthenticationError(scope_id)
    if isinstance(
        error,
        (
            botocore_exceptions.ConnectTimeoutError,
            botocore_exceptions.EndpointConnectionError,
            botocore_exceptions.ReadTimeoutError,
        ),
    ):
        return AwsApiUnavailableError(scope_id)
    if isinstance(error, botocore_exceptions.UnknownServiceError):
        return AwsDependencyError(scope_id)

    code = _client_error_code(error)
    status = _client_http_status(error)
    if code in _AUTHENTICATION_ERROR_CODES or status == 401:
        return AwsAuthenticationError(scope_id)
    if code in _PERMISSION_ERROR_CODES or status == 403:
        return AwsPermissionError(scope_id)
    if code in _REGION_ERROR_CODES and not assume_role:
        return AwsRegionNotAccessibleError(scope_id)
    if code in _UNAVAILABLE_ERROR_CODES or status in {408, 429} or (
        status is not None and status >= 500
    ):
        return AwsApiUnavailableError(scope_id)
    if isinstance(error, botocore_exceptions.BotoCoreError):
        return AwsAdapterError(scope_id)
    return AwsAdapterError(scope_id)


def _google_metadata_id_token(audience: str) -> str:
    try:
        from google.auth.compute_engine.credentials import IDTokenCredentials
        from google.auth.transport.requests import Request
    except ModuleNotFoundError as error:
        raise AwsDependencyError("federation") from error

    request = Request()
    credentials = IDTokenCredentials(
        request,
        target_audience=_require_non_empty_string("audience", audience),
        use_metadata_identity_endpoint=True,
    )
    credentials.refresh(request)
    return _require_non_empty_string("google_id_token", credentials.token)


@dataclass(frozen=True, slots=True, repr=False)
class AwsTemporaryCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str

    @classmethod
    def from_response(
        cls,
        scope_id: str,
        response: object,
    ) -> "AwsTemporaryCredentials":
        if not isinstance(response, Mapping):
            raise AwsAuthenticationError(scope_id)
        credentials = response.get("Credentials")
        if not isinstance(credentials, Mapping):
            raise AwsAuthenticationError(scope_id)
        try:
            return cls(
                access_key_id=_require_non_empty_string(
                    "AccessKeyId",
                    credentials.get("AccessKeyId"),
                ),
                secret_access_key=_require_non_empty_string(
                    "SecretAccessKey",
                    credentials.get("SecretAccessKey"),
                ),
                session_token=_require_non_empty_string(
                    "SessionToken",
                    credentials.get("SessionToken"),
                ),
            )
        except (TypeError, ValueError) as error:
            raise AwsAuthenticationError(scope_id) from error

    def client_kwargs(self) -> dict[str, str]:
        return {
            "aws_access_key_id": self.access_key_id,
            "aws_secret_access_key": self.secret_access_key,
            "aws_session_token": self.session_token,
        }


class AwsFederation:
    def __init__(
        self,
        *,
        hub_role_arn: str,
        audience: str,
        sts_region: str,
        client_factory: AwsClientFactory,
        id_token_provider: GoogleIdTokenProvider,
        public_client_options: Mapping[str, Any] | None = None,
        signed_client_options: Mapping[str, Any] | None = None,
    ) -> None:
        self._hub_role_arn = _require_non_empty_string(
            "hub_role_arn",
            hub_role_arn,
        )
        self._audience = _require_non_empty_string("audience", audience)
        self._sts_region = _require_non_empty_string("sts_region", sts_region)
        self._client_factory = client_factory
        self._id_token_provider = id_token_provider
        self._public_client_options = dict(public_client_options or {})
        self._signed_client_options = dict(signed_client_options or {})

    @classmethod
    def from_google_oidc(
        cls,
        hub_role_arn: str,
        audience: str,
        sts_region: str,
    ) -> "AwsFederation":
        try:
            import boto3
            from botocore import UNSIGNED
            from botocore.config import Config
        except ModuleNotFoundError as error:
            raise AwsDependencyError("federation") from error
        return cls(
            hub_role_arn=hub_role_arn,
            audience=audience,
            sts_region=sts_region,
            client_factory=boto3.client,
            id_token_provider=_google_metadata_id_token,
            public_client_options={
                "config": Config(
                    signature_version=UNSIGNED,
                    retries={"mode": "standard", "max_attempts": 5},
                )
            },
            signed_client_options={
                "config": Config(
                    retries={"mode": "standard", "max_attempts": 5},
                )
            },
        )

    def assume_spoke_role(
        self,
        *,
        expected_account_id: str,
        spoke_role_arn: str,
        external_id: str | None = None,
    ) -> AwsTemporaryCredentials:
        account_id = _require_non_empty_string(
            "expected_account_id",
            expected_account_id,
        )
        role_arn = _require_non_empty_string("spoke_role_arn", spoke_role_arn)
        normalized_external_id = (
            _require_non_empty_string("external_id", external_id)
            if external_id is not None
            else None
        )
        try:
            id_token = self._id_token_provider(self._audience)
        except AwsAdapterError:
            raise
        except Exception as error:
            raise AwsAuthenticationError(account_id) from error

        try:
            public_sts = self._client_factory(
                "sts",
                region_name=self._sts_region,
                **self._public_client_options,
            )
            hub_response = public_sts.assume_role_with_web_identity(
                RoleArn=self._hub_role_arn,
                RoleSessionName=f"msg-broker-hub-{uuid4().hex[:12]}",
                WebIdentityToken=id_token,
                DurationSeconds=3600,
            )
            hub_credentials = AwsTemporaryCredentials.from_response(
                "federation",
                hub_response,
            )
            hub_sts = self._client_factory(
                "sts",
                region_name=self._sts_region,
                **hub_credentials.client_kwargs(),
                **self._signed_client_options,
            )
            assume_role_request: dict[str, object] = {
                "RoleArn": role_arn,
                "RoleSessionName": (
                    f"msg-broker-{account_id}-{uuid4().hex[:8]}"
                ),
                "DurationSeconds": 3600,
            }
            if normalized_external_id is not None:
                assume_role_request["ExternalId"] = normalized_external_id
            spoke_response = hub_sts.assume_role(
                **assume_role_request,
            )
            spoke_credentials = AwsTemporaryCredentials.from_response(
                account_id,
                spoke_response,
            )
            spoke_sts = self._client_factory(
                "sts",
                region_name=self._sts_region,
                **spoke_credentials.client_kwargs(),
                **self._signed_client_options,
            )
            identity = spoke_sts.get_caller_identity()
        except AwsAdapterError:
            raise
        except Exception as error:
            raise _translate_aws_error(
                account_id,
                error,
                assume_role=True,
            ) from error

        actual_account_id = (
            identity.get("Account") if isinstance(identity, Mapping) else None
        )
        if actual_account_id != account_id:
            raise AwsAccountMismatchError(account_id)
        return spoke_credentials


_INVENTORY_INSTANCE_STATES = frozenset(
    {
        "pending",
        "running",
        "stopping",
        "stopped",
    }
)


def _instance_name(instance: Mapping[str, Any]) -> str:
    for tag in instance.get("Tags") or ():
        if (
            isinstance(tag, Mapping)
            and tag.get("Key") == "Name"
            and (value := _optional_non_empty_string(tag.get("Value")))
        ):
            return value
    return _require_non_empty_string("InstanceId", instance.get("InstanceId"))


def _instance_status(instance: Mapping[str, Any]) -> str:
    state = instance.get("State")
    name = state.get("Name") if isinstance(state, Mapping) else None
    return _require_non_empty_string("instance.State.Name", name).upper()


def _instance_zone(instance: Mapping[str, Any]) -> str | None:
    placement = instance.get("Placement")
    if not isinstance(placement, Mapping):
        return None
    return _optional_non_empty_string(placement.get("AvailabilityZone"))


def _instance_architecture(instance: Mapping[str, Any]) -> Architecture | None:
    value = _optional_non_empty_string(instance.get("Architecture"))
    if value is None:
        return None
    normalized = value.upper()
    if normalized in {"I386", "X86_64", "X86_64_MAC"}:
        return Architecture.AMD64
    if normalized in {"ARM64", "ARM64_MAC", "AARCH64"}:
        return Architecture.ARM64
    return None


class AwsAdapter:
    def __init__(self, region: str, ec2_client: AwsEc2Client) -> None:
        self._region = _require_non_empty_string("region", region)
        self._ec2_client = ec2_client

    @classmethod
    def from_credentials(
        cls,
        region: str,
        credentials: AwsTemporaryCredentials,
    ) -> "AwsAdapter":
        normalized_region = _require_non_empty_string("region", region)
        try:
            import boto3
            from botocore.config import Config
        except ModuleNotFoundError as error:
            raise AwsDependencyError(normalized_region) from error
        try:
            return cls(
                region=normalized_region,
                ec2_client=boto3.client(
                    "ec2",
                    region_name=normalized_region,
                    config=Config(
                        retries={"mode": "standard", "max_attempts": 5},
                    ),
                    **credentials.client_kwargs(),
                ),
            )
        except Exception as error:
            raise _translate_aws_error(normalized_region, error) from error

    def _instances(self) -> tuple[Mapping[str, Any], ...]:
        paginator = self._ec2_client.get_paginator("describe_instances")
        instances: list[Mapping[str, Any]] = []
        pages = paginator.paginate(
            Filters=[
                {
                    "Name": "instance-state-name",
                    "Values": sorted(_INVENTORY_INSTANCE_STATES),
                }
            ]
        )
        for page in pages:
            for reservation in page.get("Reservations") or ():
                if not isinstance(reservation, Mapping):
                    continue
                for instance in reservation.get("Instances") or ():
                    if isinstance(instance, Mapping):
                        instances.append(instance)
        return tuple(instances)

    def _instance_type_capacity(
        self,
        instances: tuple[Mapping[str, Any], ...],
    ) -> dict[str, tuple[int | None, int | None]]:
        instance_types = tuple(
            sorted(
                {
                    value
                    for instance in instances
                    if (
                        value := _optional_non_empty_string(
                            instance.get("InstanceType")
                        )
                    )
                }
            )
        )
        capacities: dict[str, tuple[int | None, int | None]] = {}
        for batch in _chunks(instance_types, 100):
            response = self._ec2_client.describe_instance_types(
                InstanceTypes=list(batch)
            )
            for item in response.get("InstanceTypes") or ():
                if not isinstance(item, Mapping):
                    continue
                instance_type = _optional_non_empty_string(
                    item.get("InstanceType")
                )
                if instance_type is None:
                    continue
                vcpu_info = item.get("VCpuInfo")
                memory_info = item.get("MemoryInfo")
                vcpus = (
                    _optional_non_negative_int(vcpu_info.get("DefaultVCpus"))
                    if isinstance(vcpu_info, Mapping)
                    else None
                )
                memory_mib = (
                    _optional_non_negative_int(memory_info.get("SizeInMiB"))
                    if isinstance(memory_info, Mapping)
                    else None
                )
                capacities[instance_type] = (
                    vcpus * 1000 if vcpus is not None else None,
                    memory_mib,
                )
        return capacities

    def _attached_ebs_storage(
        self,
        instances: tuple[Mapping[str, Any], ...],
    ) -> dict[str, int]:
        volume_ids_by_instance: dict[str, tuple[str, ...]] = {}
        all_volume_ids: set[str] = set()
        for instance in instances:
            instance_id = _require_non_empty_string(
                "InstanceId",
                instance.get("InstanceId"),
            )
            volume_ids = tuple(
                volume_id
                for mapping in instance.get("BlockDeviceMappings") or ()
                if isinstance(mapping, Mapping)
                and isinstance(mapping.get("Ebs"), Mapping)
                and (
                    volume_id := _optional_non_empty_string(
                        mapping["Ebs"].get("VolumeId")
                    )
                )
                is not None
            )
            volume_ids_by_instance[instance_id] = volume_ids
            all_volume_ids.update(volume_ids)

        sizes_gib: dict[str, int] = {}
        sorted_volume_ids = tuple(sorted(all_volume_ids))
        for batch in _chunks(sorted_volume_ids, 500):
            response = self._ec2_client.describe_volumes(VolumeIds=list(batch))
            for volume in response.get("Volumes") or ():
                if not isinstance(volume, Mapping):
                    continue
                volume_id = _optional_non_empty_string(volume.get("VolumeId"))
                size_gib = _optional_non_negative_int(volume.get("Size"))
                if volume_id is not None and size_gib is not None:
                    sizes_gib[volume_id] = size_gib

        if set(sizes_gib) != all_volume_ids:
            raise AwsResourceLookupError(self._region)
        return {
            instance_id: sum(sizes_gib[volume_id] for volume_id in volume_ids)
            * 1024
            for instance_id, volume_ids in volume_ids_by_instance.items()
        }

    def list_instances(self) -> tuple[ProviderInstanceSummary, ...]:
        try:
            instances = self._instances()
            capacities = self._instance_type_capacity(instances)
            storage_by_instance = self._attached_ebs_storage(instances)
            summaries: list[ProviderInstanceSummary] = []
            for instance in instances:
                instance_id = _require_non_empty_string(
                    "InstanceId",
                    instance.get("InstanceId"),
                )
                instance_type = _require_non_empty_string(
                    "InstanceType",
                    instance.get("InstanceType"),
                )
                cpu_millicores, memory_mib = capacities.get(
                    instance_type,
                    (None, None),
                )
                summaries.append(
                    ProviderInstanceSummary(
                        provider_scope_id=self._region,
                        provider_instance_id=instance_id,
                        name=_instance_name(instance),
                        region=self._region,
                        zone=_instance_zone(instance),
                        status=_instance_status(instance),
                        machine_type=instance_type,
                        internal_ip=_optional_non_empty_string(
                            instance.get("PrivateIpAddress")
                        ),
                        external_ip=_optional_non_empty_string(
                            instance.get("PublicIpAddress")
                        ),
                        architecture=_instance_architecture(instance),
                        provider_capacity_cpu_millicores=cpu_millicores,
                        provider_capacity_memory_mib=memory_mib,
                        provider_capacity_storage_mib=storage_by_instance[
                            instance_id
                        ],
                    )
                )
            return tuple(summaries)
        except AwsAdapterError:
            raise
        except Exception as error:
            raise _translate_aws_error(self._region, error) from error
