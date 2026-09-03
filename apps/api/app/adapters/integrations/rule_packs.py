"""Ed25519 verification for company data-quality rule packs."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.domain.assurance import TrustedRuleSigningKey
from app.domain.integrations import DataQualityRule, DataQualityRulePack

_MEMBERS = {"manifest.json", "rules.json", "signature.ed25519"}


def verify_data_quality_rule_pack(
    package: bytes, trusted_keys: list[TrustedRuleSigningKey]
) -> DataQualityRulePack:
    if not package or len(package) > 2 * 1024 * 1024:
        raise ValueError("data-quality rule pack exceeds its size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            members = archive.infolist()
            if (
                {member.filename for member in members} != _MEMBERS
                or len(members) != len(_MEMBERS)
                or any(member.flag_bits & 0x1 for member in members)
                or sum(member.file_size for member in members) > 4 * 1024 * 1024
            ):
                raise ValueError("data-quality rule pack archive is invalid")
            manifest_bytes = archive.read("manifest.json")
            rules_bytes = archive.read("rules.json")
            signature = base64.b64decode(
                archive.read("signature.ed25519"), validate=True
            )
    except (KeyError, zipfile.BadZipFile) as error:
        raise ValueError("data-quality rule pack archive is invalid") from error
    manifest = json.loads(manifest_bytes)
    required = {"pack_id", "version", "issuer", "key_id", "rules_sha256"}
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise ValueError("data-quality rule pack manifest is invalid")
    if hashlib.sha256(rules_bytes).hexdigest() != manifest["rules_sha256"]:
        raise ValueError("data-quality rule hash does not match the manifest")
    key = next(
        (value for value in trusted_keys if value.key_id == manifest["key_id"]), None
    )
    if key is None:
        raise PermissionError("data-quality signing key is not trusted")
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    public_key = Ed25519PublicKey.from_public_bytes(
        base64.b64decode(key.public_key_base64, validate=True)
    )
    try:
        public_key.verify(signature, canonical)
    except InvalidSignature as error:
        raise PermissionError("data-quality signature is invalid") from error
    decoded = json.loads(rules_bytes)
    if not isinstance(decoded, list) or any(not isinstance(value, dict) for value in decoded):
        raise ValueError("data-quality rules must be an array")
    rules = [
        DataQualityRule.model_validate({**value, "trusted": True}) for value in decoded
    ]
    return DataQualityRulePack(
        pack_id=manifest["pack_id"],
        version=manifest["version"],
        issuer=manifest["issuer"],
        rules=rules,
        manifest_sha256=hashlib.sha256(canonical).hexdigest(),
        signature=base64.b64encode(signature).decode(),
    )
