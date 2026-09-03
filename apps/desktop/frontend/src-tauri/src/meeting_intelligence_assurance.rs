use anyhow::{anyhow, Context, Result};
use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct AssuranceScheduleRegistration {
    pub enabled: bool,
    pub task_name: String,
    pub cadence: String,
    pub local_time: String,
}

fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
}

fn valid_time(value: &str) -> bool {
    let Some((hours, minutes)) = value.split_once(':') else {
        return false;
    };
    hours.len() == 2
        && minutes.len() == 2
        && hours.parse::<u8>().is_ok_and(|value| value < 24)
        && minutes.parse::<u8>().is_ok_and(|value| value < 60)
}

fn task_arguments(
    schedule_id: &str,
    cadence: &str,
    local_time: &str,
    backend_path: &std::path::Path,
) -> Result<(String, Vec<String>)> {
    if !valid_identifier(schedule_id) || !valid_time(local_time) {
        return Err(anyhow!("assurance schedule fields are invalid"));
    }
    let task_name = format!("Meeting Intelligence Copilot\\Assurance {schedule_id}");
    let schedule_args: Vec<String> = match cadence {
        "daily" => vec!["/SC".into(), "DAILY".into()],
        "weekdays" => vec![
            "/SC".into(),
            "WEEKLY".into(),
            "/D".into(),
            "MON,TUE,WED,THU,FRI".into(),
        ],
        "weekly" => vec!["/SC".into(), "WEEKLY".into(), "/D".into(), "MON".into()],
        _ => return Err(anyhow!("assurance schedule cadence is invalid")),
    };
    let executable = backend_path
        .to_str()
        .ok_or_else(|| anyhow!("assurance backend path is invalid"))?;
    let command = format!("\"{executable}\" --run-scheduled-assurance {schedule_id}");
    let mut args = vec![
        "/Create".into(),
        "/TN".into(),
        task_name.clone(),
        "/TR".into(),
        command,
    ];
    args.extend(schedule_args);
    args.extend(["/ST".into(), local_time.into(), "/F".into()]);
    Ok((task_name, args))
}

#[tauri::command]
pub async fn configure_meeting_intelligence_assurance_schedule(
    schedule_id: String,
    cadence: String,
    local_time: String,
    enabled: bool,
    run_if_missed: bool,
) -> Result<AssuranceScheduleRegistration, String> {
    let backend = super::meeting_intelligence_sidecar::resolve_backend_binary()
        .map_err(|_| "The packaged assurance backend is unavailable.".to_string())?;
    let (task_name, create_args) = task_arguments(&schedule_id, &cadence, &local_time, &backend)
        .map_err(|_| "The assurance schedule is invalid.".to_string())?;
    let task_name_for_command = task_name.clone();
    let status = tokio::task::spawn_blocking(move || {
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x08000000;
            let mut command = std::process::Command::new("schtasks.exe");
            if enabled {
                command.args(&create_args);
            } else {
                command.args(["/Delete", "/TN", &task_name_for_command, "/F"]);
            }
            let status = command.creation_flags(CREATE_NO_WINDOW).status()?;
            if !status.success() || !enabled || !run_if_missed {
                return Ok(status);
            }
            let task_leaf = format!("Meeting Intelligence Assurance {schedule_id}");
            let script = concat!(
                "$task=Get-ScheduledTask -TaskPath '\\Meeting Intelligence Copilot\\' -TaskName $args[0];",
                "$task.Settings.StartWhenAvailable=$true;",
                "Set-ScheduledTask -InputObject $task | Out-Null"
            );
            std::process::Command::new("powershell.exe")
                .args([
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    script,
                    task_leaf.as_str(),
                ])
                .creation_flags(CREATE_NO_WINDOW)
                .status()
        }
        #[cfg(not(windows))]
        {
            let _ = (
                enabled,
                run_if_missed,
                create_args,
                task_name_for_command,
                schedule_id,
            );
            Err(std::io::Error::new(
                std::io::ErrorKind::Unsupported,
                "Windows Task Scheduler is required",
            ))
        }
    })
    .await
    .map_err(|_| "The assurance scheduler did not complete.".to_string())?
    .context("Task Scheduler could not be started")
    .map_err(|_| "Windows Task Scheduler is unavailable.".to_string())?;
    if !status.success() {
        return Err("Windows rejected the assurance schedule.".to_string());
    }
    Ok(AssuranceScheduleRegistration {
        enabled,
        task_name,
        cadence,
        local_time,
    })
}

#[cfg(test)]
mod tests {
    use super::{task_arguments, valid_identifier, valid_time};
    use std::path::Path;

    #[test]
    fn schedule_fields_reject_command_injection() {
        assert!(valid_identifier("daily-solution-assurance"));
        assert!(!valid_identifier("daily & whoami"));
        assert!(valid_time("08:30"));
        assert!(!valid_time("24:00"));
    }

    #[test]
    fn weekday_task_is_bounded_and_runs_headless_backend_mode() {
        let (_, args) = task_arguments(
            "daily-solution-assurance",
            "weekdays",
            "08:00",
            Path::new(
                r"C:\Program Files\Meeting Intelligence Copilot\meeting-intelligence-backend.exe",
            ),
        )
        .unwrap();
        let rendered = args.join(" ");
        assert!(rendered.contains("--run-scheduled-assurance daily-solution-assurance"));
        assert!(rendered.contains("MON,TUE,WED,THU,FRI"));
    }
}
