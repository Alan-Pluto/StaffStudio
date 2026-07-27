from app.capabilities.errors import CapabilityErrorInfo, CapabilityProviderError


def test_provider_error_is_classified_without_parsing_message() -> None:
    error = CapabilityProviderError(
        CapabilityErrorInfo(
            code="KNOWLEDGE_UPSTREAM_TIMEOUT",
            message="Knowledge service did not answer before the deadline",
            retryable=True,
            request_id="req-1",
        )
    )

    assert str(error) == "Knowledge service did not answer before the deadline"
    assert error.info.code == "KNOWLEDGE_UPSTREAM_TIMEOUT"
    assert error.info.retryable is True
