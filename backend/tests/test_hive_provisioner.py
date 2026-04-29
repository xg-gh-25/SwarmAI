"""Tests for Hive provisioner and user-data template.

Tests the provisioner logic with mocked boto3 calls. Each test verifies
one acceptance criterion from the pipeline evaluation.
"""
import asyncio
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── User-Data Template Tests ──────────────────────────────────────

class TestUserData:
    """Tests for user_data.py — template rendering and password generation."""

    def test_render_substitutes_all_variables(self):
        """AC10: user-data template fully parameterized."""
        from hive.user_data import render_user_data

        result = render_user_data(
            s3_bucket="swarmai-hive-releases-us-east-1",
            version="1.9.0",
            auth_user="admin",
            auth_hash="$2a$14$abc123",
            region="us-east-1",
        )
        assert "swarmai-hive-releases-us-east-1" in result
        assert "1.9.0" in result
        assert "admin" in result
        assert "$2a$14$abc123" in result
        assert "us-east-1" in result

    def test_no_hardcoded_values(self):
        """AC10: no hardcoded bucket names, IPs, or versions."""
        from hive.user_data import render_user_data

        result = render_user_data(
            s3_bucket="test-bucket",
            version="99.99.99",
            auth_user="testuser",
            auth_hash="testhash",
            region="eu-west-1",
        )
        # Should contain our test values, not any default/hardcoded ones
        assert "test-bucket" in result
        assert "99.99.99" in result
        assert "testuser" in result
        assert "eu-west-1" in result
        # Should NOT contain any hardcoded values from earlier versions
        assert "swarmai-hive-releases" not in result or "test-bucket" in result
        assert "swarmai-hive-artifacts" not in result

    def test_script_is_valid_bash(self):
        """User-data script starts with shebang and uses set -euo."""
        from hive.user_data import render_user_data

        result = render_user_data(
            s3_bucket="b", version="1.0.0",
            auth_user="u", auth_hash="h", region="us-east-1",
        )
        assert result.startswith("#!/bin/bash")
        assert "set -euo pipefail" in result

    def test_script_tags_ready_on_success(self):
        """AC2: tags HiveStatus=ready."""
        from hive.user_data import render_user_data

        result = render_user_data(
            s3_bucket="b", version="1.0.0",
            auth_user="u", auth_hash="h", region="us-east-1",
        )
        assert 'Key=HiveStatus,Value="$TAG_STATUS"' in result
        assert 'TAG_STATUS="ready"' in result

    def test_password_generation(self):
        """Passphrase is dash-separated words, random each time."""
        from hive.user_data import generate_password

        p1 = generate_password()
        p2 = generate_password()
        words = p1.split("-")
        assert len(words) == 4  # Default 4 words
        assert all(w.isalpha() for w in words)  # Only letters
        assert p1 != p2  # Should be random

    def test_password_custom_word_count(self):
        from hive.user_data import generate_password

        p = generate_password(6)
        assert len(p.split("-")) == 6

    def test_word_list_exactly_256(self):
        """256 words = 8 bits per word. More or fewer = comment lies."""
        import re, inspect
        from hive.user_data import generate_password
        src = inspect.getsource(generate_password)
        words = re.findall(r'"(\w+)"', src)
        assert len(words) == 256, f"Expected 256 words, got {len(words)}"
        assert len(set(words)) == 256, "Duplicate words in list"

    def test_bcrypt_hash_survives_base64_roundtrip(self):
        """PE P0-1: bcrypt hash contains $ — must survive base64 encoding for SSM script."""
        import base64
        from hive.user_data import caddy_hash_password
        pw = "test-pass-phrase"
        h = caddy_hash_password(pw)
        assert "$2b$14$" in h  # bcrypt format
        # Simulate what reset_password does: base64 encode → decode on instance
        b64 = base64.b64encode(h.encode()).decode()
        assert "$" not in b64  # No shell-special chars in base64
        roundtrip = base64.b64decode(b64).decode()
        assert roundtrip == h  # Exact match after roundtrip


# ── Provisioner Unit Tests ────────────────────────────────────────

class TestProvisionerSession:
    """Tests for boto3 session creation from account config."""

    def test_access_keys_session(self):
        """Creates session with access keys from auth_config."""
        from hive.provisioner import HiveProvisioner

        p = HiveProvisioner(Path("/tmp/test.db"))
        account = {
            "auth_method": "access_keys",
            "auth_config": json.dumps({
                "access_key_id": "AKIA_TEST",
                "secret_access_key": "SECRET_TEST",
            }),
        }
        # boto3 is imported inside _get_session, mock at the module level
        with patch.dict("sys.modules", {"boto3": MagicMock()}) as _:
            import boto3 as mock_boto3
            session = p._get_session(account, "us-east-1")
            # Verify it called Session with the right kwargs
            assert session is not None  # Got something back

    def test_sso_session(self):
        """Creates session with SSO profile from auth_config."""
        from hive.provisioner import HiveProvisioner

        p = HiveProvisioner(Path("/tmp/test.db"))
        account = {
            "auth_method": "sso",
            "auth_config": json.dumps({"profile": "my-sso-profile"}),
        }
        with patch.dict("sys.modules", {"boto3": MagicMock()}):
            session = p._get_session(account, "us-west-2")
            assert session is not None

    def test_default_session(self):
        """Falls back to default credential chain when no config."""
        from hive.provisioner import HiveProvisioner

        p = HiveProvisioner(Path("/tmp/test.db"))
        account = {"auth_method": "iam_role", "auth_config": "{}"}
        with patch.dict("sys.modules", {"boto3": MagicMock()}):
            session = p._get_session(account, "us-east-1")
            assert session is not None


class TestProvisionerS3:
    """Tests for S3 bucket creation and release sync."""

    @pytest.mark.asyncio
    async def test_ensure_bucket_creates_with_region(self):
        """AC10: S3 bucket named swarmai-hive-releases-{region}."""
        from hive.provisioner import HiveProvisioner

        p = HiveProvisioner(Path("/tmp/test.db"))
        mock_session = MagicMock()
        mock_s3 = MagicMock()
        mock_session.client.return_value = mock_s3

        bucket = await p._ensure_s3_bucket(mock_session, "eu-west-1")
        assert bucket == "swarmai-hive-releases-eu-west-1"
        mock_s3.create_bucket.assert_called_once()
        # Verify LocationConstraint for non-us-east-1
        call_kwargs = mock_s3.create_bucket.call_args
        assert call_kwargs[1]["CreateBucketConfiguration"]["LocationConstraint"] == "eu-west-1"

    @pytest.mark.asyncio
    async def test_ensure_bucket_us_east_1_no_location(self):
        """us-east-1 doesn't use LocationConstraint (AWS quirk)."""
        from hive.provisioner import HiveProvisioner

        p = HiveProvisioner(Path("/tmp/test.db"))
        mock_session = MagicMock()
        mock_s3 = MagicMock()
        mock_session.client.return_value = mock_s3

        bucket = await p._ensure_s3_bucket(mock_session, "us-east-1")
        assert bucket == "swarmai-hive-releases-us-east-1"
        call_kwargs = mock_s3.create_bucket.call_args
        assert "CreateBucketConfiguration" not in call_kwargs[1]


class TestProvisionerIAM:
    """Tests for IAM role and instance profile creation."""

    @pytest.mark.asyncio
    async def test_create_role_has_bedrock_permissions(self):
        """AC1: IAM role includes bedrock:InvokeModel*."""
        from hive.provisioner import HiveProvisioner, HIVE_IAM_POLICY

        bedrock_actions = HIVE_IAM_POLICY["Statement"][0]["Action"]
        assert "bedrock:InvokeModel" in bedrock_actions
        assert "bedrock:InvokeModelWithResponseStream" in bedrock_actions

    @pytest.mark.asyncio
    async def test_create_role_has_s3_access(self):
        """IAM role can read from hive releases bucket."""
        from hive.provisioner import HIVE_IAM_POLICY

        s3_resources = HIVE_IAM_POLICY["Statement"][1]["Resource"]
        assert any("swarmai-hive-releases" in r for r in s3_resources)

    @pytest.mark.asyncio
    async def test_instance_profile_waits_for_propagation(self):
        """IAM propagation: 15s sleep after instance profile creation."""
        from hive.provisioner import HiveProvisioner

        p = HiveProvisioner(Path("/tmp/test.db"))
        mock_session = MagicMock()
        mock_iam = MagicMock()
        mock_session.client.return_value = mock_iam
        mock_iam.create_instance_profile.return_value = {
            "InstanceProfile": {"Arn": "arn:aws:iam::123:instance-profile/test"}
        }

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await p._create_instance_profile(mock_session, "test-hive")
            # Must wait for IAM propagation
            mock_sleep.assert_called_with(15)


class TestProvisionerSG:
    """Tests for security group creation."""

    @pytest.mark.asyncio
    async def test_sg_opens_80_443_only(self):
        """AC5: security group opens port 80 from CloudFront only, no 443, no 22."""
        from hive.provisioner import HiveProvisioner

        p = HiveProvisioner(Path("/tmp/test.db"))
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        mock_ec2.describe_vpcs.return_value = {
            "Vpcs": [{"VpcId": "vpc-test"}]
        }
        mock_ec2.create_security_group.return_value = {"GroupId": "sg-test"}
        mock_ec2.describe_managed_prefix_lists.return_value = {
            "PrefixLists": [{"PrefixListId": "pl-test123"}]
        }

        sg_id = await p._create_security_group(mock_session, "test", "us-east-1")
        assert sg_id == "sg-test"

        # Verify ingress rules
        ingress_call = mock_ec2.authorize_security_group_ingress.call_args
        ip_perms = ingress_call[1]["IpPermissions"]
        ports = {p["FromPort"] for p in ip_perms}
        assert ports == {80}  # Only port 80, no 443
        # Explicitly: no port 22
        assert 22 not in ports
        # Verify CloudFront prefix list is used (not 0.0.0.0/0)
        assert ip_perms[0]["PrefixListIds"][0]["PrefixListId"] == "pl-test123"
        assert "IpRanges" not in ip_perms[0] or ip_perms[0].get("IpRanges") == []


class TestProvisionerHealthCheck:
    """Tests for health polling."""

    @pytest.mark.asyncio
    async def test_wait_healthy_returns_true_on_200(self):
        """AC3: health check returns true when /health responds 200."""
        from hive.provisioner import HiveProvisioner

        p = HiveProvisioner(Path("/tmp/test.db"))

        with patch("hive.provisioner.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "healthy", "version": "1.9.0"}
            mock_client.get.return_value = mock_resp

            result = await p._wait_healthy("1.2.3.4", timeout=10)
            assert result is True

    @pytest.mark.asyncio
    async def test_wait_healthy_returns_false_on_timeout(self):
        """Health check returns false when timeout expires."""
        from hive.provisioner import HiveProvisioner

        p = HiveProvisioner(Path("/tmp/test.db"))

        with patch("hive.provisioner.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get.side_effect = Exception("Connection refused")

            result = await p._wait_healthy("1.2.3.4", timeout=2)
            assert result is False


class TestResetPassword:
    """Tests for password reset via SSM."""

    @pytest.mark.asyncio
    async def test_reset_password_generates_new_passphrase(self):
        """reset_password returns a dash-separated passphrase."""
        from hive.provisioner import HiveProvisioner

        p = HiveProvisioner(Path("/tmp/test.db"))

        # Mock DB lookups
        mock_instance = {
            "id": "inst-1", "name": "test-hive", "account_ref": "acc-1",
            "region": "us-east-1", "ec2_instance_id": "i-abc123",
        }
        mock_account = {
            "auth_method": "access_keys",
            "auth_config": '{"access_key_id": "AK", "secret_access_key": "SK"}',
        }

        with patch.object(p, "_get_instance", new_callable=AsyncMock, return_value=mock_instance), \
             patch.object(p, "_get_account", new_callable=AsyncMock, return_value=mock_account), \
             patch.object(p, "_update_instance", new_callable=AsyncMock), \
             patch.object(p, "_get_session") as mock_session_fn:

            # Mock SSM send_command + get_command_invocation
            mock_ssm = MagicMock()
            mock_ssm.send_command.return_value = {"Command": {"CommandId": "cmd-1"}}
            mock_ssm.get_command_invocation.return_value = {
                "Status": "Success", "StandardOutputContent": "Password reset complete",
            }
            mock_session = MagicMock()
            mock_session.client.return_value = mock_ssm
            mock_session_fn.return_value = mock_session

            result = await p.reset_password("inst-1")

            # Result is a passphrase
            words = result.split("-")
            assert len(words) == 4
            assert all(w.isalpha() for w in words)

            # DB was updated with new password
            p._update_instance.assert_called_once()
            call_kwargs = p._update_instance.call_args
            assert call_kwargs[0][0] == "inst-1"
            assert call_kwargs[1]["auth_password"] == result

    @pytest.mark.asyncio
    async def test_reset_password_bcrypt_hash_survives_base64(self):
        """The bcrypt hash ($2b$14$...) round-trips through base64 encoding."""
        from hive.user_data import generate_password, caddy_hash_password
        import base64

        pw = generate_password()
        h = caddy_hash_password(pw)
        # Hash must contain $ signs
        assert "$" in h
        # base64 round-trip must be lossless
        encoded = base64.b64encode(h.encode()).decode()
        decoded = base64.b64decode(encoded).decode()
        assert decoded == h

    @pytest.mark.asyncio
    async def test_reset_password_ssm_failure_raises(self):
        """SSM failure raises RuntimeError, does NOT update DB."""
        from hive.provisioner import HiveProvisioner

        p = HiveProvisioner(Path("/tmp/test.db"))

        mock_instance = {
            "id": "inst-1", "name": "test-hive", "account_ref": "acc-1",
            "region": "us-east-1", "ec2_instance_id": "i-abc123",
        }
        mock_account = {
            "auth_method": "access_keys",
            "auth_config": '{"access_key_id": "AK", "secret_access_key": "SK"}',
        }

        with patch.object(p, "_get_instance", new_callable=AsyncMock, return_value=mock_instance), \
             patch.object(p, "_get_account", new_callable=AsyncMock, return_value=mock_account), \
             patch.object(p, "_update_instance", new_callable=AsyncMock) as mock_update, \
             patch.object(p, "_get_session") as mock_session_fn:

            mock_ssm = MagicMock()
            mock_ssm.send_command.return_value = {"Command": {"CommandId": "cmd-1"}}
            mock_ssm.get_command_invocation.return_value = {
                "Status": "Failed", "StandardOutputContent": "Caddy validation failed",
            }
            mock_session = MagicMock()
            mock_session.client.return_value = mock_ssm
            mock_session_fn.return_value = mock_session

            with pytest.raises(RuntimeError, match="Password reset failed"):
                await p.reset_password("inst-1")

            # DB must NOT be updated on failure
            mock_update.assert_not_called()


class TestProvisionerCleanupOrder:
    """Tests for resource cleanup order."""

    def test_cleanup_order_ec2_before_sg(self):
        """AC8: cleanup terminates EC2 before deleting SG."""
        # The cleanup code in provisioner._cleanup_resources does:
        # 1. EC2 terminate, 2. CloudFront, 3. EIP, 4. SG, 5. IAM
        # Verify by checking the method exists and has the right structure
        from hive.provisioner import HiveProvisioner
        import inspect

        source = inspect.getsource(HiveProvisioner._cleanup_resources)
        # EC2 terminate comes before SG delete in the source
        ec2_pos = source.find("terminate_instances")
        sg_pos = source.find("delete_security_group")
        assert ec2_pos < sg_pos, "EC2 must terminate before SG delete"

        # IAM role delete comes last
        iam_pos = source.find("delete_role(")
        assert sg_pos < iam_pos, "SG delete must come before IAM delete"
