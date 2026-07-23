"""Canonical parsing and serialization for durable provenance manifests."""

from __future__ import annotations

import json

from pydantic import ValidationError

from metadata.schemas import ProvenanceManifest


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object while rejecting duplicate member names."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _finite_constant(value: str) -> None:
    """Reject NaN and infinities, which are outside the JSON standard."""
    raise ValueError(f"non-finite JSON value {value!r} is not allowed")


def canonical_provenance_bytes(payload: dict) -> bytes:
    """Serialize one validated payload into the sole durable byte encoding."""
    return (
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def load_canonical_provenance(data: bytes, *, label: str) -> ProvenanceManifest:
    """Parse, fully validate, and require canonical bytes for one manifest."""
    try:
        payload = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_finite_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(
            f"{label} is not unambiguous finite UTF-8 JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    try:
        manifest = ProvenanceManifest.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"{label} failed provenance schema validation: {exc}") from exc
    canonical = canonical_provenance_bytes(manifest.model_dump(mode="json"))
    if data != canonical:
        raise ValueError(f"{label} is not in canonical provenance JSON encoding")
    return manifest
