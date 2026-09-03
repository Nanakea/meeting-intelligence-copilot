"""Verification for bounded, signed company assurance rule packs."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.domain.assurance import AssuranceRule, AssuranceRulePack, TrustedRuleSigningKey

_MAXIMUM_PACKAGE_BYTES = 2 * 1024 * 1024
_MAXIMUM_EXPANDED_BYTES = 4 * 1024 * 1024
_ALLOWED_MEMBERS = {"manifest.json", "rules.json", "signature.ed25519"}


def signing_key(
    *, key_id: str, label: str, public_key_base64: str
) -> TrustedRuleSigningKey:
    raw = base64.b64decode(public_key_base64, validate=True)
    if len(raw) != 32:
        raise ValueError("Ed25519 public keys must be 32 bytes")
    Ed25519PublicKey.from_public_bytes(raw)
    return TrustedRuleSigningKey(
        key_id=key_id,
        label=label,
        public_key_base64=public_key_base64,
        fingerprint_sha256=hashlib.sha256(raw).hexdigest(),
    )


def verify_rule_pack(
    package_bytes: bytes, trusted_keys: list[TrustedRuleSigningKey]
) -> AssuranceRulePack:
    if not package_bytes or len(package_bytes) > _MAXIMUM_PACKAGE_BYTES:
        raise ValueError("rule pack exceeds the package size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(package_bytes)) as archive:
            members = archive.infolist()
            names = {member.filename for member in members}
            if (
                names != _ALLOWED_MEMBERS
                or len(members) != len(_ALLOWED_MEMBERS)
                or any(member.flag_bits & 0x1 for member in members)
                or sum(member.file_size for member in members) > _MAXIMUM_EXPANDED_BYTES
            ):
                raise ValueError("rule pack archive shape is invalid")
            manifest_bytes = archive.read("manifest.json")
            rules_bytes = archive.read("rules.json")
            signature_bytes = base64.b64decode(
                archive.read("signature.ed25519"), validate=True
            )
    except (zipfile.BadZipFile, KeyError) as error:
        raise ValueError("rule pack archive is invalid") from error
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict):
        raise ValueError("rule pack manifest must be an object")
    required = {"pack_id", "version", "issuer", "key_id", "rules_sha256"}
    if set(manifest) != required:
        raise ValueError("rule pack manifest fields are invalid")
    if hashlib.sha256(rules_bytes).hexdigest() != manifest["rules_sha256"]:
        raise ValueError("rule pack rules hash mismatch")
    key = next(
        (value for value in trusted_keys if value.key_id == manifest["key_id"]), None
    )
    if key is None:
        raise PermissionError("rule pack signing key is not trusted")
    public_key = Ed25519PublicKey.from_public_bytes(
        base64.b64decode(key.public_key_base64, validate=True)
    )
    canonical_manifest = json.dumps(
        manifest, sort_keys=True, separators=(",", ":")
    ).encode()
    try:
        public_key.verify(signature_bytes, canonical_manifest)
    except InvalidSignature as exc:
        raise PermissionError("rule pack signature is invalid") from exc
    decoded_rules = json.loads(rules_bytes)
    if not isinstance(decoded_rules, list):
        raise ValueError("rule pack rules must be an array")
    rules = [AssuranceRule.model_validate(value) for value in decoded_rules]
    return AssuranceRulePack(
        pack_id=manifest["pack_id"],
        version=manifest["version"],
        issuer=manifest["issuer"],
        rules=rules,
        manifest_sha256=hashlib.sha256(canonical_manifest).hexdigest(),
        signature=base64.b64encode(signature_bytes).decode(),
        trusted=True,
    )
