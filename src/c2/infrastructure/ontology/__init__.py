"""온톨로지 그래프 스토어 인프라 구현.

- graph_store.py     : Neo4jGraphStore — Cypher 검색/적재 (원본 그대로)
- in_memory_store.py : InMemoryGraphStore — Neo4j 미접속 시 폴백 (원본 그대로)
- factory.py         : 환경변수 기반 그래프 스토어 생성 (Neo4j, 실패 시 in-memory 폴백)

두 스토어 모두 `c2.application.ports.ontology_store.OntologyStore` 포트를
구조적으로 만족한다.
"""
