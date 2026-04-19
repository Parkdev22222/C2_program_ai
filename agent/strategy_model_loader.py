"""
EXAONE Deep 전략/전술 전문 모델 로더

EXAONE Deep은 군사 전략 및 전술 추천에 특화된 추론 모델입니다.
EXAONE4로부터 전달받은 상황 분석 정보를 바탕으로 전문적인 전략/전술 권고안을 생성합니다.

주의: temperature, max_tokens는 VLLMModel.__init__()에 전달하면 안 됩니다.
      이 값들은 generate() 호출 시 별도로 전달해야 합니다.
"""
import yaml
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config" / "models_config.yaml"


def load_strategy_model(config: Optional[Dict[str, Any]] = None):
    """
    EXAONE Deep 모델을 VLLMModel로 로드합니다.

    Args:
        config: 모델 설정 딕셔너리. None이면 models_config.yaml에서 읽음.

    Returns:
        smolagents VLLMModel 인스턴스
    """
    from smolagents import VLLMModel

    if config is None:
        config = load_strategy_model_config()

    model_id = config.get("model_id", "LGAI-EXAONE/EXAONE-Deep-32B")

    # generation 파라미터는 init에 전달하지 않음 (smolagents VLLMModel 규칙)
    generation_cfg = config.pop("generation", {})

    model_kwargs = {
        k: v
        for k, v in config.items()
        if k not in ("model_id", "generation")
    }

    logger.info(f"Loading EXAONE Deep strategy model: {model_id}")
    logger.info(f"Model kwargs: {model_kwargs}")

    model = VLLMModel(
        model_id=model_id,
        **model_kwargs,
    )

    # generation 파라미터를 모델 인스턴스에 메타데이터로 보관
    model._strategy_generation_kwargs = generation_cfg
    logger.info("EXAONE Deep strategy model loaded successfully")
    return model


def load_strategy_model_from_config_file():
    """
    models_config.yaml에서 strategy_model 설정을 읽어 EXAONE Deep 모델을 로드합니다.
    """
    config = load_strategy_model_config()
    return load_strategy_model(config)


def load_strategy_model_config() -> Dict[str, Any]:
    """models_config.yaml의 strategy_model 섹션을 반환합니다."""
    with open(CONFIG_PATH) as f:
        full_config = yaml.safe_load(f)
    return dict(full_config.get("strategy_model", {}))
