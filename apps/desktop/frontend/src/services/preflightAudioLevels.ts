export interface PreflightAudioLevel {
  device_name: string;
  device_type: 'input' | 'output';
  rms_level: number;
  peak_level: number;
  is_active: boolean;
}

export interface SelectedPreflightAudioLevels {
  microphone: PreflightAudioLevel | null;
  systemAudio: PreflightAudioLevel | null;
}

export function selectPreflightAudioLevels(
  levels: PreflightAudioLevel[],
  microphoneDeviceName: string,
  systemAudioDeviceName: string,
): SelectedPreflightAudioLevels {
  return {
    microphone:
      levels.find(
        (level) =>
          level.device_type === 'input' &&
          level.device_name === microphoneDeviceName,
      ) ?? null,
    systemAudio:
      levels.find(
        (level) =>
          level.device_type === 'output' &&
          level.device_name === systemAudioDeviceName,
      ) ?? null,
  };
}
