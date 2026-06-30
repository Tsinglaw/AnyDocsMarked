import pytest

from makeitdown.cloud_consent import (
    CLOUD_NOTICE, CloudConsentRequired, has_consent, require_cloud_consent,
)


def test_flag_grants_consent():
    assert has_consent(True, env={}) is True


def test_env_grants_consent():
    assert has_consent(False, env={"MAKEITDOWN_CLOUD_CONSENT": "1"}) is True
    assert has_consent(False, env={"MAKEITDOWN_CLOUD_CONSENT": "yes"}) is True


def test_no_consent_by_default():
    assert has_consent(False, env={}) is False
    assert has_consent(False, env={"MAKEITDOWN_CLOUD_CONSENT": "0"}) is False


def test_require_raises_with_guidance_when_absent():
    with pytest.raises(CloudConsentRequired) as exc:
        require_cloud_consent(False, env={})
    msg = str(exc.value)
    assert "local" in msg and "--cloud-consent" in msg


def test_require_passes_with_consent():
    require_cloud_consent(True, env={})  # must not raise


def test_notice_mentions_upload_and_local():
    assert "上传" in CLOUD_NOTICE and "local" in CLOUD_NOTICE
