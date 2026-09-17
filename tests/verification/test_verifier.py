import pytest

from runtime.state.state import AgentState
from runtime.verification.result import VerificationResult
from runtime.verification.verifier import Verifier


class TestVerifier(Verifier):
    async def verify(self, state: AgentState) -> VerificationResult:
        return VerificationResult(
            status="PASS",
            summary="Verification passed.",
        )


def test_verifier_is_abstract():

    assert Verifier.__abstractmethods__ == {"verify"}


@pytest.mark.asyncio
async def test_verifier_can_be_implemented():

    verifier = TestVerifier()

    result = await verifier.verify(None)

    assert result.status == "PASS"
    assert result.summary == "Verification passed."
