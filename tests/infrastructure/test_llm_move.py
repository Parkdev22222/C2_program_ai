"""Task 13: LLM 인프라 이동 — c2.infrastructure.llm + shim 검증.

- 새 경로(c2.infrastructure.llm.*)에서 구현체가 임포트 가능한지 확인.
- 옛 경로(agent.*)가 새 경로와 동일 객체(identity)를 재노출하는 shim인지 확인.
"""


# ── vllm_client ─────────────────────────────────────────────────────────
def test_vllm_client_importable_from_new_path():
    from c2.infrastructure.llm.vllm_client import (
        VLLMServerClient,
        resolve_base_url,
        normalize_messages,
        DEFAULT_API_KEY,
        LAUNCH_HINT,
    )

    assert VLLMServerClient is not None
    assert callable(resolve_base_url)
    assert callable(normalize_messages)
    assert DEFAULT_API_KEY == "EMPTY"
    assert isinstance(LAUNCH_HINT, str)


def test_vllm_client_shim_identity():
    import agent.vllm_client as old
    import c2.infrastructure.llm.vllm_client as new

    assert old.VLLMServerClient is new.VLLMServerClient
    assert old.resolve_base_url is new.resolve_base_url
    assert old.normalize_messages is new.normalize_messages
    assert old.DEFAULT_API_KEY is new.DEFAULT_API_KEY
    assert old.LAUNCH_HINT is new.LAUNCH_HINT


# ── model_loader ────────────────────────────────────────────────────────
def test_model_loader_importable_from_new_path():
    from c2.infrastructure.llm.model_loader import (
        EXAONE4ServedModel,
        load_exaone_model,
        load_model_from_config_file,
        load_exaone_model_config,
    )

    assert EXAONE4ServedModel is not None
    assert callable(load_exaone_model)
    assert callable(load_model_from_config_file)
    assert callable(load_exaone_model_config)


def test_model_loader_shim_identity():
    import agent.model_loader as old
    import c2.infrastructure.llm.model_loader as new

    assert old.EXAONE4ServedModel is new.EXAONE4ServedModel
    assert old.load_exaone_model is new.load_exaone_model
    assert old.load_model_from_config_file is new.load_model_from_config_file
    assert old.load_exaone_model_config is new.load_exaone_model_config


def test_model_loader_config_path_still_resolves():
    """모듈 위치가 바뀌어도 CONFIG_PATH가 여전히 실제 config 파일을 가리켜야 한다."""
    from c2.infrastructure.llm.model_loader import CONFIG_PATH

    assert CONFIG_PATH.exists()
    assert CONFIG_PATH.name == "models_config.yaml"


def test_model_loader_internal_cross_import_uses_new_path():
    """model_loader가 vllm_client를 참조할 때 새 c2.infrastructure.llm 경로를 통해야 한다."""
    import c2.infrastructure.llm.model_loader as new_model_loader
    import c2.infrastructure.llm.vllm_client as new_vllm_client

    assert new_model_loader.VLLMServerClient is new_vllm_client.VLLMServerClient
    assert new_model_loader.resolve_base_url is new_vllm_client.resolve_base_url


# ── langgraph_llm ───────────────────────────────────────────────────────
def test_langgraph_llm_importable_from_new_path():
    from c2.infrastructure.llm.langgraph_llm import (
        build_chat_llm,
        describe_llm_target,
        resolve_provider,
        resolve_base_url,
    )

    assert callable(build_chat_llm)
    assert callable(describe_llm_target)
    assert callable(resolve_provider)
    assert callable(resolve_base_url)


def test_langgraph_llm_shim_identity():
    import agent.langgraph_llm as old
    import c2.infrastructure.llm.langgraph_llm as new

    assert old.build_chat_llm is new.build_chat_llm
    assert old.describe_llm_target is new.describe_llm_target
    assert old.resolve_provider is new.resolve_provider
    assert old.resolve_base_url is new.resolve_base_url


def test_langgraph_llm_config_path_still_resolves():
    from c2.infrastructure.llm.langgraph_llm import _CONFIG_PATH

    assert _CONFIG_PATH.exists()
    assert _CONFIG_PATH.name == "models_config.yaml"
