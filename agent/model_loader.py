"""
EXAONE4 메인 에이전트 모델 로더 (EXAONE-4.0-32B-AWQ)

주의: temperature, max_tokens는 VLLMModel.__init__()에 전달하면 안 됩니다.
      이 값들은 generate() 호출 시 별도로 전달됩니다.
"""
import yaml
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "models_config.yaml"


def load_exaone_model(config: Optional[Dict[str, Any]] = None):
    """
    EXAONE4 모델을 VLLMModel로 로드합니다.

    Args:
        config: 모델 설정 딕셔너리. None이면 models_config.yaml에서 읽음.

    Returns:
        smolagents VLLMModel 인스턴스
    """
    from smolagents import VLLMModel

    if config is None:
        config = load_exaone_model_config()

    model_id = config.get("model_id_awq") or config.get("model_id", "LGAI-EXAONE/EXAONE-4.0-32B-AWQ")

    # generation 파라미터는 init에 전달하지 않음
    generation_cfg = config.pop("generation", {})

    model_kwargs = {
        k: v
        for k, v in config.items()
        if k not in ("model_id", "model_id_awq", "quantization", "generation")
    }

    logger.info(f"Loading EXAONE4 main agent model: {model_id}")
    logger.info(f"Model kwargs: {model_kwargs}")

    model = VLLMModel(
        model_id=model_id,
        **model_kwargs,
    )
    model._exaone4_generation_kwargs = generation_cfg
    logger.info("EXAONE4 model loaded successfully")
    return model


def load_model_from_config_file():
    """
    models_config.yaml에서 agent_model 설정을 읽어 EXAONE4 모델을 로드합니다.
    """
    config = load_exaone_model_config()
    return load_exaone_model(config)


def load_exaone_model_config() -> Dict[str, Any]:
    """models_config.yaml의 agent_model 섹션을 반환합니다."""
    with open(CONFIG_PATH) as f:
        full_config = yaml.safe_load(f)
    return dict(full_config.get("agent_model", {}))
