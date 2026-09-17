import pytest

from runtime.llm.base import LLMClient


def test_llm_client_is_abstract():
    with pytest.raises(TypeError):
        LLMClient()
