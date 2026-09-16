"""S3 policy response compatibility; not proof of live storage erasure."""

from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

from accessforge_persistence.evidence.objectstore import (
    ArtifactStoreError,
    ObjectStoreUnavailable,
    S3ArtifactStore,
    S3Settings,
)


def store_with_client() -> tuple[S3ArtifactStore, Mock]:
    client = Mock()
    client.get_bucket_versioning.return_value = {}
    client.get_bucket_lifecycle_configuration.return_value = {}
    client.get_bucket_replication.return_value = {"ReplicationConfiguration": {}}
    with patch("boto3.client", return_value=client):
        store = S3ArtifactStore(S3Settings("https://storage.example", "key", "secret", "bucket"))
    return store, client


def test_empty_successful_policies_allow_retirement_check() -> None:
    store, client = store_with_client()
    client.get_bucket_versioning.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
    store.assert_retirement_supported()
    client.put_object.assert_not_called()


def test_aws_missing_policy_errors_remain_supported() -> None:
    store, client = store_with_client()
    for method, code in (
        (client.get_bucket_lifecycle_configuration, "NoSuchLifecycleConfiguration"),
        (client.get_bucket_replication, "ReplicationConfigurationNotFoundError"),
    ):
        method.side_effect = ClientError({"Error": {"Code": code}}, "GetPolicy")
    store.assert_retirement_supported()


@pytest.mark.parametrize(
    ("operation", "response"),
    [
        ("get_bucket_versioning", {"Status": "Enabled"}),
        ("get_bucket_versioning", {"Status": "Suspended"}),
        ("get_bucket_versioning", {"Status": None}),
        ("get_bucket_versioning", {"Unknown": True}),
        ("get_bucket_lifecycle_configuration", {"Rules": [{"Status": "Disabled"}]}),
        ("get_bucket_lifecycle_configuration", {"Rules": []}),
        ("get_bucket_lifecycle_configuration", {"Unknown": {}}),
        ("get_bucket_lifecycle_configuration", None),
        ("get_bucket_replication", {"ReplicationConfiguration": {"Rules": []}}),
        ("get_bucket_replication", {"ReplicationConfiguration": None}),
        ("get_bucket_replication", {"ReplicationConfiguration": {}, "Unknown": True}),
    ],
)
def test_nonempty_or_unknown_policy_refuses_before_overwrite(
    operation: str, response: object
) -> None:
    store, client = store_with_client()
    getattr(client, operation).return_value = response
    with pytest.raises(ArtifactStoreError):
        store.retire_create_only(key="candidate")
    client.put_object.assert_not_called()


@pytest.mark.parametrize("code", ["AccessDenied", "NotImplemented", "InternalError"])
def test_other_provider_errors_do_not_mean_absent_policy(code: str) -> None:
    store, client = store_with_client()
    client.get_bucket_replication.side_effect = ClientError(
        {"Error": {"Code": code}}, "GetBucketReplication"
    )
    with pytest.raises(ObjectStoreUnavailable):
        store.retire_create_only(key="candidate")
    client.put_object.assert_not_called()


def test_policy_rechecked_after_tombstone_write() -> None:
    store, client = store_with_client()
    client.get_bucket_versioning.side_effect = [{}, {"Status": "Enabled"}]
    body = Mock()
    body.read.return_value = b""
    client.get_object.return_value = {"Body": body, "ContentLength": 0}
    with pytest.raises(ArtifactStoreError):
        store.retire_create_only(key="candidate")
    client.put_object.assert_called_once()
    assert client.get_bucket_versioning.call_count == 2
    body.close.assert_called_once()
