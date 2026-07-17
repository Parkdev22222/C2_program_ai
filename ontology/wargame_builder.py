"""[shim] 워게임→온톨로지 변환기는 c2.application.ontology.wargame_builder 로 이동됨."""
from c2.application.ontology.wargame_builder import (  # noqa: F401  [shim]
    WARGAME_SCENARIO_ID,
    WargameOntologyBuilder,
    seed_entity_ids,
    xy_to_latlon,
)
