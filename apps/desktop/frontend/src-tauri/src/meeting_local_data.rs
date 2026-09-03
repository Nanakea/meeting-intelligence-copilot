use serde_json::Value;
use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};

const OWNERSHIP_MARKER: &str = ".meetily-recording";
const OWNERSHIP_MARKER_CONTENT: &[u8] = b"meetily-local-recording-v1\n";
const MAX_LEGACY_METADATA_BYTES: u64 = 64 * 1024;

pub fn mark_meeting_folder(path: &Path) -> std::io::Result<()> {
    fs::write(path.join(OWNERSHIP_MARKER), OWNERSHIP_MARKER_CONTENT)
}

fn has_ownership_marker(path: &Path) -> bool {
    fs::read(path.join(OWNERSHIP_MARKER)).is_ok_and(|contents| contents == OWNERSHIP_MARKER_CONTENT)
}

fn has_legacy_meetily_metadata(path: &Path) -> bool {
    let metadata_path = path.join("metadata.json");
    let Ok(metadata) = metadata_path.metadata() else {
        return false;
    };
    if !metadata.is_file() || metadata.len() > MAX_LEGACY_METADATA_BYTES {
        return false;
    }
    let Ok(contents) = fs::read(metadata_path) else {
        return false;
    };
    let Ok(Value::Object(document)) = serde_json::from_slice::<Value>(&contents) else {
        return false;
    };
    document.get("version").is_some_and(Value::is_string)
        && document.get("transcript_file").and_then(Value::as_str) == Some("transcripts.json")
        && document
            .get("created_at")
            .and_then(Value::as_str)
            .is_some_and(|created_at| chrono::DateTime::parse_from_rfc3339(created_at).is_ok())
        && path.join("transcripts.json").is_file()
        && has_legacy_timestamped_folder_name(path)
}

fn has_legacy_timestamped_folder_name(path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return false;
    };
    let bytes = name.as_bytes();
    if bytes.len() < 18 {
        return false;
    }
    let suffix = &bytes[bytes.len() - 17..];
    suffix.iter().enumerate().all(|(index, byte)| match index {
        0 | 11 => *byte == b'_',
        5 | 8 | 14 => *byte == b'-',
        1..=4 | 6..=7 | 9..=10 | 12..=13 | 15..=16 => byte.is_ascii_digit(),
        _ => false,
    })
}

pub fn delete_meeting_folder(path: Option<&str>) -> Result<(), &'static str> {
    let Some(path) = path.filter(|value| !value.trim().is_empty()) else {
        return Ok(());
    };
    let path = Path::new(path);
    match path.try_exists() {
        Ok(false) => return Ok(()),
        Ok(true) => {}
        Err(_) => return Err("The local recording folder could not be inspected"),
    }
    let metadata = fs::symlink_metadata(path)
        .map_err(|_| "The local recording folder could not be inspected")?;
    if !path.is_absolute()
        || path.components().count() < 3
        || !metadata.is_dir()
        || metadata.file_type().is_symlink()
        || (!has_ownership_marker(path) && !has_legacy_meetily_metadata(path))
    {
        return Err("The local recording folder is not recognized as application data");
    }
    fs::remove_dir_all(path).map_err(|_| "The local recording folder could not be deleted")
}

/// Remove crash remnants that were marked by the recorder but never reached the
/// database. The scan is intentionally shallow and excludes every retained
/// database path; legacy metadata alone never grants sweep authority.
pub fn delete_orphaned_meeting_folders(
    recording_root: &Path,
    retained_folder_paths: &[PathBuf],
) -> Result<(usize, usize), &'static str> {
    if !recording_root.is_absolute() {
        return Err("The local recordings folder is not safe to inspect");
    }
    match recording_root.try_exists() {
        Ok(false) => return Ok((0, 0)),
        Ok(true) => {}
        Err(_) => return Err("The local recordings folder could not be inspected"),
    }
    let root_metadata = fs::symlink_metadata(recording_root)
        .map_err(|_| "The local recordings folder could not be inspected")?;
    if !root_metadata.is_dir() || root_metadata.file_type().is_symlink() {
        return Err("The local recordings folder is not safe to inspect");
    }

    let retained: HashSet<PathBuf> = retained_folder_paths
        .iter()
        .map(|path| fs::canonicalize(path).unwrap_or_else(|_| path.clone()))
        .collect();
    let entries = fs::read_dir(recording_root)
        .map_err(|_| "The local recordings folder could not be inspected")?;
    let mut deleted = 0;
    let mut failures = 0;
    for entry in entries {
        let Ok(entry) = entry else {
            failures += 1;
            continue;
        };
        let path = entry.path();
        let Ok(metadata) = fs::symlink_metadata(&path) else {
            failures += 1;
            continue;
        };
        if !metadata.is_dir() || metadata.file_type().is_symlink() || !has_ownership_marker(&path) {
            continue;
        }
        let identity = fs::canonicalize(&path).unwrap_or_else(|_| path.clone());
        if retained.contains(&identity) {
            continue;
        }
        let Some(path) = path.to_str() else {
            failures += 1;
            continue;
        };
        match delete_meeting_folder(Some(path)) {
            Ok(()) => deleted += 1,
            Err(_) => failures += 1,
        }
    }
    Ok((deleted, failures))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn marked_partial_recording_can_be_deleted() {
        let root = tempfile::tempdir().unwrap();
        let folder = root.path().join("recordings").join("meeting");
        fs::create_dir_all(&folder).unwrap();
        mark_meeting_folder(&folder).unwrap();
        fs::write(folder.join("audio.mp4"), b"partial").unwrap();

        delete_meeting_folder(folder.to_str()).unwrap();

        assert!(!folder.exists());
    }

    #[test]
    fn legacy_metadata_identifies_an_existing_meetily_folder() {
        let root = tempfile::tempdir().unwrap();
        let folder = root
            .path()
            .join("recordings")
            .join("Legacy meeting_2026-08-13_16-30");
        fs::create_dir_all(&folder).unwrap();
        fs::write(
            folder.join("metadata.json"),
            br#"{"version":"1.0","created_at":"2026-08-13T00:00:00Z","transcript_file":"transcripts.json"}"#,
        )
        .unwrap();
        fs::write(folder.join("transcripts.json"), b"[]").unwrap();

        delete_meeting_folder(folder.to_str()).unwrap();

        assert!(!folder.exists());
    }

    #[test]
    fn metadata_alone_never_authorizes_recursive_deletion() {
        let root = tempfile::tempdir().unwrap();
        let unrelated = root.path().join("documents").join("customer-project");
        fs::create_dir_all(&unrelated).unwrap();
        fs::write(
            unrelated.join("metadata.json"),
            br#"{"version":"1.0","created_at":"2026-08-13T00:00:00Z","transcript_file":"transcripts.json"}"#,
        )
        .unwrap();
        fs::write(unrelated.join("transcripts.json"), b"[]").unwrap();

        assert!(delete_meeting_folder(unrelated.to_str()).is_err());
        assert!(unrelated.exists());
    }

    #[test]
    fn unrelated_directories_are_never_recursively_deleted() {
        let root = tempfile::tempdir().unwrap();
        let unrelated = root.path().join("documents").join("meeting");
        fs::create_dir_all(&unrelated).unwrap();
        fs::write(unrelated.join("audio.mp4"), b"private").unwrap();

        assert!(delete_meeting_folder(unrelated.to_str()).is_err());
        assert!(unrelated.exists());
    }

    #[test]
    fn missing_folder_cleanup_is_idempotent() {
        let root = tempfile::tempdir().unwrap();
        let missing = root.path().join("recordings").join("missing");

        delete_meeting_folder(missing.to_str()).unwrap();
    }

    #[test]
    fn same_title_recordings_always_get_distinct_owned_folders() {
        let root = tempfile::tempdir().unwrap();
        let first = crate::audio::audio_processing::create_meeting_folder(
            &root.path().to_path_buf(),
            "Daily Sync",
            false,
        )
        .unwrap();
        let second = crate::audio::audio_processing::create_meeting_folder(
            &root.path().to_path_buf(),
            "Daily Sync",
            false,
        )
        .unwrap();

        assert_ne!(first, second);
        assert!(has_ownership_marker(&first));
        assert!(has_ownership_marker(&second));
    }

    #[test]
    fn long_or_blank_titles_stay_safe_without_changing_metadata_titles() {
        let root = tempfile::tempdir().unwrap();
        let long = crate::audio::audio_processing::create_meeting_folder(
            &root.path().to_path_buf(),
            &"a".repeat(1_000),
            false,
        )
        .unwrap();
        let blank = crate::audio::audio_processing::create_meeting_folder(
            &root.path().to_path_buf(),
            "   ",
            false,
        )
        .unwrap();

        assert!(long.file_name().unwrap().to_string_lossy().len() < 140);
        assert!(blank
            .file_name()
            .unwrap()
            .to_string_lossy()
            .starts_with("Meeting_"));
    }

    #[test]
    fn orphan_sweep_deletes_only_unreferenced_marked_direct_children() {
        let root = tempfile::tempdir().unwrap();
        let recordings = root.path().join("recordings");
        let orphan = recordings.join("orphan");
        let retained = recordings.join("retained");
        let unrelated = recordings.join("unrelated");
        let nested = recordings.join("container").join("nested");
        for folder in [&orphan, &retained, &unrelated, &nested] {
            fs::create_dir_all(folder).unwrap();
        }
        mark_meeting_folder(&orphan).unwrap();
        mark_meeting_folder(&retained).unwrap();
        mark_meeting_folder(&nested).unwrap();

        let result = delete_orphaned_meeting_folders(&recordings, &[retained.clone()]).unwrap();

        assert_eq!(result, (1, 0));
        assert!(!orphan.exists());
        assert!(retained.exists());
        assert!(unrelated.exists());
        assert!(nested.exists());
    }

    #[test]
    fn orphan_sweep_rejects_a_relative_root() {
        assert!(delete_orphaned_meeting_folders(Path::new("recordings"), &[]).is_err());
    }
}
