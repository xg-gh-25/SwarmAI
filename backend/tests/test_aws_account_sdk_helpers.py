"""The account layer's SDK helpers (core/aws_account.py: client_config, sigv4_headers,
endpoint_unreachable, service_model, sdk_version).

Question: does each helper give a consumer exactly what it used to build from botocore
itself, so the consumers (carriers/agentcore, hive_agentcore, ingestion/summary_lane,
hive/agentcore_provisioner, routers/hive) import no AWS SDK and behave as before?
"""
from __future__ import annotations

from datetime import datetime
from unittest import mock

import botocore
import pytest
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.credentials import Credentials, ReadOnlyCredentials
from botocore.exceptions import (ClientError, ConnectTimeoutError, EndpointConnectionError,
                                 UnknownServiceError)

from core import aws_account

URL = "https://abc.bedrock-agentcore.us-east-1.amazonaws.com/runtimes/x/invocations?q=1"
HOST = "abc.bedrock-agentcore.us-east-1.amazonaws.com"


def test_client_config_carries_options():
    cfg = aws_account.client_config(connect_timeout=7, read_timeout=11,
                                    retries={"max_attempts": 1, "mode": "standard"},
                                    signature_version="v4")
    assert isinstance(cfg, Config)
    assert (cfg.connect_timeout, cfg.read_timeout, cfg.signature_version) == (7, 11, "v4")
    assert cfg.retries == {"max_attempts": 1, "mode": "standard"}


@pytest.mark.parametrize("token", ["session-token", None])
def test_sigv4_headers_match_botocore(token):
    at = datetime(2026, 11, 23, 10, 0, 0)
    with mock.patch("botocore.auth.get_current_datetime", return_value=at):
        got = aws_account.sigv4_headers("GET", URL, HOST,
                                        ReadOnlyCredentials("AKIDEXAMPLE", "secret", token),
                                        service="bedrock-agentcore", region="us-east-1")
        want = AWSRequest(method="GET", url=URL, headers={"host": HOST}, data=b"")
        SigV4Auth(Credentials("AKIDEXAMPLE", "secret", token), "bedrock-agentcore",
                  "us-east-1").add_auth(want)
    assert got["Authorization"] == want.headers["Authorization"]
    assert got["X-Amz-Date"] == want.headers["X-Amz-Date"] == "20261123T100000Z"
    assert ("X-Amz-Security-Token" in got) is (token is not None)
    if token:
        assert got["X-Amz-Security-Token"] == token
    # signed over the host only, with no payload-hash header
    assert "SignedHeaders=host;x-amz-date" in got["Authorization"]
    assert "X-Amz-Content-SHA256" not in got and "X-Amz-Content-Sha256" not in got


def test_sigv4_headers_refuses_keys_that_are_not_a_sessions_frozen_credentials():
    """Raw keys, or a look-alike of the frozen type, would let a module outside the
    integrity test's list sign with credentials from another source."""
    from collections import namedtuple
    lookalike = namedtuple("Frozen", "access_key secret_key token")("AKIDEXAMPLE", "secret", None)
    for bad in (lookalike, ("AKIDEXAMPLE", "secret", None),
                Credentials("AKIDEXAMPLE", "secret", None)):
        with pytest.raises(TypeError):
            aws_account.sigv4_headers("GET", URL, HOST, bad, service="bedrock-agentcore",
                                      region="us-east-1")


def test_endpoint_unreachable():
    assert aws_account.endpoint_unreachable(EndpointConnectionError(endpoint_url="https://x"))
    assert aws_account.endpoint_unreachable(ConnectTimeoutError(endpoint_url="https://x"))
    assert not aws_account.endpoint_unreachable(
        ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse"))
    assert not aws_account.endpoint_unreachable(RuntimeError("boom"))


def test_service_model_known_service():
    model = aws_account.service_model("sts")
    assert model.service_name == "sts"
    assert "GetCallerIdentity" in model.operation_names


def test_service_model_unknown_service_raises():
    with pytest.raises(UnknownServiceError):
        aws_account.service_model("no-such-service-swarmai")


def test_sdk_missing_returns_every_field_when_the_sdk_has_no_agentcore(monkeypatch):
    from hive import agentcore_provisioner as ep

    def unknown(_name):
        raise UnknownServiceError(service_name=_name, known_service_names="sts")

    monkeypatch.setattr(aws_account, "service_model", unknown)
    assert ep.sdk_missing() == list(ep.SDK_FIELDS)


def test_sdk_version():
    assert aws_account.sdk_version() == botocore.__version__


def test_summary_lane_walks_the_chain_with_the_helper():
    from core.ingestion.summary_lane import model_unreachable
    try:
        try:
            raise EndpointConnectionError(endpoint_url="https://bedrock-runtime")
        except EndpointConnectionError as inner:
            raise RuntimeError("summary failed") from inner
    except RuntimeError as outer:
        assert model_unreachable(outer)
    assert not model_unreachable(RuntimeError("the summary itself was bad"))
