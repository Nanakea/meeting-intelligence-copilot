export type StartRecordingCommandArguments = Record<string, string | null> & {
  micDeviceName: string | null;
  systemDeviceName: string | null;
  meetingName: string;
};

// Tauri command arguments are camelCase even when Rust parameters are snake_case.
export function buildStartRecordingCommandArguments(
  micDeviceName: string | null,
  systemDeviceName: string | null,
  meetingName: string,
): StartRecordingCommandArguments {
  return {
    micDeviceName,
    systemDeviceName,
    meetingName,
  };
}
