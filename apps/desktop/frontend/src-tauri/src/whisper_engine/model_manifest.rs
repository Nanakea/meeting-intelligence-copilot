use anyhow::{anyhow, Context, Result};
use ring::{digest, signature};
use serde::{Deserialize, Serialize};
use std::fs::File;
use std::io::{BufReader, Read};
use std::path::{Path, PathBuf};

const MANIFEST_BYTES: &[u8] = include_bytes!("../../resources/whisper-model-manifest-v1.json");
const MANIFEST_SIGNATURE_HEX: &str = include_str!("../../resources/whisper-model-manifest-v1.sig");
const MANIFEST_PUBLIC_KEY_HEX: &str =
    "3774b51acb47e894cbb2a5f9626c9a000048dfdc545dbf0879c3b8883522780a";

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct WhisperModelArtifactManifest {
    pub schema_version: u8,
    pub revision: String,
    pub artifacts: Vec<WhisperModelArtifact>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct WhisperModelArtifact {
    pub model_id: String,
    pub file_name: String,
    pub immutable_url: String,
    pub byte_count: u64,
    pub sha256: String,
    pub supported_languages: Vec<String>,
    pub license_reference: String,
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum WhisperModelIntegrity {
    Verified,
    Missing,
    Unapproved,
    InvalidSize,
    InvalidHash,
    InvalidFormat,
    ManifestInvalid,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct WhisperModelReadiness {
    pub model_id: String,
    pub manifest_revision: Option<String>,
    pub integrity: WhisperModelIntegrity,
    pub supported_language: bool,
    pub expected_bytes: Option<u64>,
    pub actual_bytes: Option<u64>,
}

impl WhisperModelReadiness {
    pub fn ready(&self) -> bool {
        self.integrity == WhisperModelIntegrity::Verified && self.supported_language
    }
}

fn decode_hex<const N: usize>(value: &str) -> Result<[u8; N]> {
    let value = value.trim();
    if value.len() != N * 2 {
        return Err(anyhow!("invalid encoded length"));
    }
    let mut decoded = [0_u8; N];
    for (index, byte) in decoded.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&value[index * 2..index * 2 + 2], 16)
            .map_err(|_| anyhow!("invalid encoded value"))?;
    }
    Ok(decoded)
}

fn verify_manifest_bytes(manifest: &[u8], signature_hex: &str) -> Result<()> {
    let public_key = decode_hex::<32>(MANIFEST_PUBLIC_KEY_HEX)?;
    let signature = decode_hex::<64>(signature_hex)?;
    signature::UnparsedPublicKey::new(&signature::ED25519, public_key)
        .verify(manifest, &signature)
        .map_err(|_| anyhow!("model manifest signature is invalid"))
}

pub fn verified_manifest() -> Result<WhisperModelArtifactManifest> {
    verify_manifest_bytes(MANIFEST_BYTES, MANIFEST_SIGNATURE_HEX)?;
    let manifest: WhisperModelArtifactManifest =
        serde_json::from_slice(MANIFEST_BYTES).context("model manifest is not valid JSON")?;
    if manifest.schema_version != 1 || manifest.artifacts.is_empty() {
        return Err(anyhow!("model manifest schema is unsupported"));
    }
    for artifact in &manifest.artifacts {
        validate_artifact_definition(artifact)?;
    }
    Ok(manifest)
}

fn validate_artifact_definition(artifact: &WhisperModelArtifact) -> Result<()> {
    if artifact.model_id.trim().is_empty()
        || artifact.file_name != format!("ggml-{}.bin", artifact.model_id)
        || artifact.byte_count < 1_000_000
        || artifact.sha256.len() != 64
        || artifact.supported_languages.is_empty()
    {
        return Err(anyhow!("model manifest artifact is invalid"));
    }
    decode_hex::<32>(&artifact.sha256)?;
    let url = url::Url::parse(&artifact.immutable_url)
        .map_err(|_| anyhow!("model artifact URL is invalid"))?;
    if url.scheme() != "https"
        || url.host_str() != Some("huggingface.co")
        || url.username() != ""
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(anyhow!("model artifact URL is not approved"));
    }
    Ok(())
}

pub fn approved_artifact(model_id: &str) -> Result<(String, WhisperModelArtifact)> {
    let manifest = verified_manifest()?;
    let artifact = manifest
        .artifacts
        .into_iter()
        .find(|candidate| candidate.model_id == model_id)
        .ok_or_else(|| anyhow!("model is not present in the approved manifest"))?;
    Ok((manifest.revision, artifact))
}

pub fn inspect_artifact_file(
    path: &Path,
    artifact: &WhisperModelArtifact,
) -> WhisperModelIntegrity {
    let metadata = match path.metadata() {
        Ok(metadata) if metadata.is_file() => metadata,
        _ => return WhisperModelIntegrity::Missing,
    };
    if metadata.len() != artifact.byte_count {
        return WhisperModelIntegrity::InvalidSize;
    }

    let file = match File::open(path) {
        Ok(file) => file,
        Err(_) => return WhisperModelIntegrity::InvalidFormat,
    };
    let mut reader = BufReader::with_capacity(1024 * 1024, file);
    let mut header = [0_u8; 4];
    if reader.read_exact(&mut header).is_err() || &header != b"ggml" {
        return WhisperModelIntegrity::InvalidFormat;
    }

    let mut context = digest::Context::new(&digest::SHA256);
    context.update(&header);
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let read = match reader.read(&mut buffer) {
            Ok(0) => break,
            Ok(read) => read,
            Err(_) => return WhisperModelIntegrity::InvalidHash,
        };
        context.update(&buffer[..read]);
    }
    let expected = match decode_hex::<32>(&artifact.sha256) {
        Ok(expected) => expected,
        Err(_) => return WhisperModelIntegrity::ManifestInvalid,
    };
    if context.finish().as_ref() == expected {
        WhisperModelIntegrity::Verified
    } else {
        WhisperModelIntegrity::InvalidHash
    }
}

pub async fn model_readiness(
    model_id: &str,
    model_path: PathBuf,
    language: Option<&str>,
) -> WhisperModelReadiness {
    let (revision, artifact) = match approved_artifact(model_id) {
        Ok(value) => value,
        Err(error) => {
            let integrity = if error.to_string().contains("not present") {
                WhisperModelIntegrity::Unapproved
            } else {
                WhisperModelIntegrity::ManifestInvalid
            };
            return WhisperModelReadiness {
                model_id: model_id.to_string(),
                manifest_revision: None,
                integrity,
                supported_language: false,
                expected_bytes: None,
                actual_bytes: model_path.metadata().ok().map(|metadata| metadata.len()),
            };
        }
    };
    let supported_language = language.is_some_and(|language| {
        artifact
            .supported_languages
            .iter()
            .any(|supported| supported == language)
    });
    let actual_bytes = model_path.metadata().ok().map(|metadata| metadata.len());
    let expected_bytes = artifact.byte_count;
    let integrity =
        tokio::task::spawn_blocking(move || inspect_artifact_file(&model_path, &artifact))
            .await
            .unwrap_or(WhisperModelIntegrity::InvalidHash);
    WhisperModelReadiness {
        model_id: model_id.to_string(),
        manifest_revision: Some(revision),
        integrity,
        supported_language,
        expected_bytes: Some(expected_bytes),
        actual_bytes,
    }
}

pub fn digest_matches(expected_sha256: &str, digest: &digest::Digest) -> Result<bool> {
    Ok(digest.as_ref() == decode_hex::<32>(expected_sha256)?)
}

pub fn has_ggml_header(path: &Path) -> bool {
    let mut header = [0_u8; 4];
    File::open(path)
        .and_then(|mut file| file.read_exact(&mut header))
        .is_ok()
        && &header == b"ggml"
}

#[cfg(target_os = "windows")]
pub fn replace_atomically(source: &Path, destination: &Path) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let source: Vec<u16> = source.as_os_str().encode_wide().chain(Some(0)).collect();
    let destination: Vec<u16> = destination
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    let result = unsafe {
        MoveFileExW(
            source.as_ptr(),
            destination.as_ptr(),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
    };
    if result == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(not(target_os = "windows"))]
pub fn replace_atomically(source: &Path, destination: &Path) -> std::io::Result<()> {
    std::fs::rename(source, destination)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bundled_manifest_signature_and_artifact_are_pinned() {
        let manifest = verified_manifest().unwrap();
        assert_eq!(manifest.schema_version, 1);
        assert_eq!(manifest.artifacts.len(), 1);
        let artifact = &manifest.artifacts[0];
        assert_eq!(artifact.model_id, "large-v3-turbo-q5_0");
        assert_eq!(artifact.byte_count, 574_041_195);
        assert_eq!(
            artifact.sha256,
            "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"
        );
        assert_eq!(artifact.supported_languages, ["en", "ja", "ko"]);
    }

    #[test]
    fn manifest_signature_rejects_tampering() {
        let mut tampered = MANIFEST_BYTES.to_vec();
        tampered[0] ^= 1;
        assert!(verify_manifest_bytes(&tampered, MANIFEST_SIGNATURE_HEX).is_err());
    }

    #[tokio::test]
    async fn unapproved_model_is_never_ready() {
        let readiness = model_readiness("tiny.en", PathBuf::from("missing.bin"), Some("ko")).await;
        assert_eq!(readiness.integrity, WhisperModelIntegrity::Unapproved);
        assert!(!readiness.ready());
    }
}
