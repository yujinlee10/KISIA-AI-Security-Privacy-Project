# 온톨로지의 뼈대: Node와 Ontology
#
# node_id는 이름표(label)와 무관한 고정 문자열이다. 예) "LOC:C:CH", "AGE:10Y30", "OCCP:251"
# 각 QI는 뿌리 "<QI>:ANY" 하나를 가진 나무다. Ontology는 만든 뒤 바뀌지 않는다.

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Mapping

from core.schemas import QI_TYPES

ROOT_SUFFIX = "ANY"


def root_id(attribute: str) -> str:
    return f"{attribute}:{ROOT_SUFFIX}"


@dataclass(frozen=True)
class Node:
    """
    node_id   → 고정 ID
    attribute → 8개 QI 코드 중 하나
    parent_id → 부모 node. 뿌리(ANY)만 None
    level     → 단계 이름 (예: "exact", "5y", "10y", "city", "country", "unit", "minor", "any")
    label_ko / label_en → 사람이 읽는 표현. 응답을 고쳐 쓸 때와 Seed 문장을 채울 때 쓴다
    """
    node_id: str
    attribute: str
    parent_id: str | None
    level: str
    label_ko: str
    label_en: str

    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    @property
    def phrase(self) -> str:
        """고쳐 쓰기 지시에 넣을 표현: '한국어 (영어)'. 둘이 같으면 하나만."""
        if self.label_ko == self.label_en:
            return self.label_en
        return f"{self.label_ko} ({self.label_en})"


class OntologyError(ValueError):
    """온톨로지 구성이 잘못됐을 때 (부모 없음, 순환, 중복 ID 등)."""


class Ontology:
    def __init__(self, nodes: Iterable[Node], *, version: str, sources: Mapping[str, str] | None = None) -> None:
        """
        input : nodes, version (사람이 붙이는 버전 이름), sources (표 이름 → 내용 해시. 기록용)
        output : 없음

        만들 때 구조를 검사한다: ID 중복, 없는 부모, 다른 QI를 부모로 가리킴, QI마다 뿌리 하나, 순환.
        """
        self._nodes: dict[str, Node] = {}
        for node in nodes:
            if node.node_id in self._nodes:
                raise OntologyError(f"node_id 중복: {node.node_id}")
            self._nodes[node.node_id] = node

        self._children: dict[str, list[str]] = {node_id: [] for node_id in self._nodes}
        for node in self._nodes.values():
            if node.attribute not in QI_TYPES:
                raise OntologyError(f"{node.node_id}: 알 수 없는 QI {node.attribute}")
            if node.parent_id is None:
                if node.node_id != root_id(node.attribute):
                    raise OntologyError(f"{node.node_id}: 부모가 없는 node는 뿌리(ANY)뿐이어야 합니다.")
                continue
            parent = self._nodes.get(node.parent_id)
            if parent is None:
                raise OntologyError(f"{node.node_id}: 부모 {node.parent_id}가 없습니다.")
            if parent.attribute != node.attribute:
                raise OntologyError(f"{node.node_id}: 부모가 다른 QI({parent.attribute})입니다.")
            self._children[node.parent_id].append(node.node_id)

        for attribute in QI_TYPES:
            if root_id(attribute) not in self._nodes:
                raise OntologyError(f"{attribute}: 뿌리 node가 없습니다.")

        self._ancestors: dict[str, tuple[str, ...]] = {}
        for node_id in self._nodes:
            self._ancestors[node_id] = self._walk_up(node_id)

        self.version = version
        self.sources = dict(sources or {})
        self.content_hash = self._hash()

    # ── 조회 ─────────────────────────────────────────────

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def node(self, node_id: str) -> Node:
        try:
            return self._nodes[node_id]
        except KeyError:
            raise KeyError(f"온톨로지에 없는 node: {node_id}") from None

    def nodes(self, attribute: str | None = None) -> tuple[Node, ...]:
        return tuple(n for n in self._nodes.values() if attribute is None or n.attribute == attribute)

    def parent(self, node_id: str) -> str | None:
        return self.node(node_id).parent_id

    def children(self, node_id: str) -> tuple[str, ...]:
        self.node(node_id)
        return tuple(self._children[node_id])

    def ancestors(self, node_id: str) -> tuple[str, ...]:
        """부모부터 뿌리까지 (가까운 것 먼저). 자기 자신은 빼고."""
        self.node(node_id)
        return self._ancestors[node_id]

    def has_descendant(self, node_id: str) -> bool:
        return bool(self.children(node_id))

    def depth(self, node_id: str) -> int:
        """뿌리까지 몇 단계인지. 뿌리는 0."""
        return len(self.ancestors(node_id))

    # ── 관계 ─────────────────────────────────────────────

    def is_same_or_descendant(self, node_id: str, of: str) -> bool:
        """node_id가 of와 같거나 그 아래에 있는지."""
        return node_id == of or of in self.ancestors(node_id)

    def relation(self, existing: str, candidate: str) -> str:
        """
        input : existing (이미 공개된 node), candidate (새로 나온 node). 같은 QI여야 한다
        output : "SAME" / "DESCENDANT" (candidate가 더 구체적) / "ANCESTOR" (덜 구체적) / "INCOMPATIBLE"
        """
        if self.node(existing).attribute != self.node(candidate).attribute:
            raise OntologyError(f"서로 다른 QI의 node를 비교했습니다: {existing}, {candidate}")
        if existing == candidate:
            return "SAME"
        if existing in self.ancestors(candidate):
            return "DESCENDANT"
        if candidate in self.ancestors(existing):
            return "ANCESTOR"
        return "INCOMPATIBLE"

    def most_specific(self, node_ids: Iterable[str]) -> str | None:
        """
        input : 같은 QI의 node 여러 개
        output : 모두가 한 줄기(조상–자손)에 있으면 그중 가장 구체적인 node, 아니면 None
        """
        ordered = sorted(set(node_ids), key=self.depth)
        if not ordered:
            return None
        deepest = ordered[-1]
        if all(self.is_same_or_descendant(deepest, other) for other in ordered):
            return deepest
        return None

    # ── 내부 ─────────────────────────────────────────────

    def _walk_up(self, node_id: str) -> tuple[str, ...]:
        chain: list[str] = []
        seen = {node_id}
        current = self._nodes[node_id].parent_id
        while current is not None:
            if current in seen:
                raise OntologyError(f"{node_id}: 부모 관계가 순환합니다.")
            seen.add(current)
            chain.append(current)
            current = self._nodes[current].parent_id
        return tuple(chain)

    def _hash(self) -> str:
        hasher = hashlib.sha256()
        for node_id in sorted(self._nodes):
            node = self._nodes[node_id]
            hasher.update(f"{node.node_id}|{node.parent_id}|{node.level}\n".encode("utf-8"))
        for name in sorted(self.sources):
            hasher.update(f"{name}={self.sources[name]}\n".encode("utf-8"))
        return hasher.hexdigest()
