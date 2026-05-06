"""Minimal JSON:API document wrappers.

SimplePractice's `/frontend/*` API speaks the JSON:API specification: every
response is `{ data: ..., included: [...] }`. Each ``included`` entry is keyed
by ``(type, id)`` and referenced from ``data.relationships.<rel>.data.{type,id}``.

We don't need a full JSON:API library — we just need:

* A typed wrapper around the response dict (`Document`, `Resource`).
* An ``IncludedIndex`` that resolves ``(type, id)`` lookups in O(1) so the
  extraction layer can pull, e.g., the progress note attached to an
  appointment without quadratic searches over ``included``.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Resource:
    """One JSON:API resource: ``{type, id, attributes, relationships}``."""

    type: str
    id: str
    attributes: dict[str, Any] = field(default_factory=dict)
    relationships: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Resource:
        return cls(
            type=str(raw.get("type", "")),
            id=str(raw.get("id", "")),
            attributes=dict(raw.get("attributes") or {}),
            relationships=dict(raw.get("relationships") or {}),
        )

    def attr(self, name: str, default: Any = None) -> Any:
        return self.attributes.get(name, default)

    def rel_ref(self, name: str) -> tuple[str, str] | None:
        """Return ``(type, id)`` for a single-resource relationship, or None."""
        rel = self.relationships.get(name)
        if not isinstance(rel, dict):
            return None
        data = rel.get("data")
        if not isinstance(data, dict):
            return None
        rtype = data.get("type")
        rid = data.get("id")
        if not rtype or not rid:
            return None
        return (str(rtype), str(rid))

    def rel_refs(self, name: str) -> list[tuple[str, str]]:
        """Return ``[(type, id), ...]`` for a to-many relationship."""
        rel = self.relationships.get(name)
        if not isinstance(rel, dict):
            return []
        data = rel.get("data")
        if not isinstance(data, list):
            return []
        out: list[tuple[str, str]] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            rtype = entry.get("type")
            rid = entry.get("id")
            if rtype and rid:
                out.append((str(rtype), str(rid)))
        return out


class IncludedIndex:
    """O(1) lookup table over a JSON:API ``included`` array."""

    __slots__ = ("_by_key",)

    def __init__(self, included: Iterable[dict[str, Any]] | None = None) -> None:
        self._by_key: dict[tuple[str, str], Resource] = {}
        if included:
            self.extend(included)

    def extend(self, resources: Iterable[dict[str, Any] | Resource]) -> None:
        for raw in resources:
            res = raw if isinstance(raw, Resource) else Resource.from_dict(raw)
            if res.type and res.id:
                self._by_key[(res.type, res.id)] = res

    def get(self, rtype: str, rid: str) -> Resource | None:
        return self._by_key.get((rtype, rid))

    def by_ref(self, ref: tuple[str, str] | None) -> Resource | None:
        if ref is None:
            return None
        return self._by_key.get(ref)

    def of_type(self, rtype: str) -> list[Resource]:
        return [r for (t, _), r in self._by_key.items() if t == rtype]

    def __contains__(self, key: tuple[str, str]) -> bool:
        return key in self._by_key

    def __len__(self) -> int:
        return len(self._by_key)

    def __iter__(self) -> Iterator[Resource]:
        return iter(self._by_key.values())


@dataclass(slots=True)
class Document:
    """JSON:API document. ``data`` is either a single resource or a list."""

    data: Resource | list[Resource]
    included: IncludedIndex
    meta: dict[str, Any] = field(default_factory=dict)
    links: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Document:
        raw_data = raw.get("data")
        if isinstance(raw_data, list):
            data: Resource | list[Resource] = [Resource.from_dict(d) for d in raw_data if isinstance(d, dict)]
        elif isinstance(raw_data, dict):
            data = Resource.from_dict(raw_data)
        else:
            data = []
        return cls(
            data=data,
            included=IncludedIndex(raw.get("included") or []),
            meta=dict(raw.get("meta") or {}),
            links=dict(raw.get("links") or {}),
        )

    def primary(self) -> Resource:
        if isinstance(self.data, Resource):
            return self.data
        if self.data:
            return self.data[0]
        raise ValueError("Document has no primary resource")

    def primary_list(self) -> list[Resource]:
        if isinstance(self.data, list):
            return list(self.data)
        return [self.data]

    def all_resources(self) -> list[Resource]:
        """Flatten data + included into a single list (useful for searches)."""
        out: list[Resource] = list(self.primary_list())
        out.extend(self.included)
        return out

    def find(self, rtype: str, rid: str) -> Resource | None:
        """Find any resource (primary or included) by ``(type, id)``."""
        for res in self.primary_list():
            if res.type == rtype and res.id == rid:
                return res
        return self.included.get(rtype, rid)

    def merge_included(self, other: Document) -> None:
        """Pull another document's ``included`` into ours.

        Useful when stitching multiple per-appointment payloads onto a
        timeline document.
        """
        self.included.extend(other.included)
        for res in other.primary_list():
            self.included.extend([res])
