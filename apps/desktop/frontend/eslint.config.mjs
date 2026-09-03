import { dirname } from "path";
import { fileURLToPath } from "url";
import { FlatCompat } from "@eslint/eslintrc";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
});

// Upstream legacy surfaces still contain explicit `any` at external API and
// editor boundaries. Keep them visible as warnings while preserving error-level
// enforcement everywhere else, including every live-intelligence file.
const legacyAnyFiles = [
  "src/app/meeting-details/page-content.tsx",
  "src/app/meeting-details/page.tsx",
  "src/app/settings/page.tsx",
  "src/components/AISummary/BlockNoteSummaryView.tsx",
  "src/components/BuiltInModelManager.tsx",
  "src/components/MeetingDetails/RetranscribeDialog.tsx",
  "src/components/MeetingDetails/SummaryGeneratorButtonGroup.tsx",
  "src/components/MeetingDetails/SummaryPanel.tsx",
  "src/components/ModelSettingsModal.tsx",
  "src/components/molecules/form-components/form-input-item.tsx",
  "src/components/molecules/form-components/form-input-switch.tsx",
  "src/components/molecules/form-components/form-select-item.tsx",
  "src/components/Sidebar/index.tsx",
  "src/components/Sidebar/SidebarProvider.tsx",
  "src/components/SummaryModelSettings.tsx",
  "src/components/TranscriptRecovery/TranscriptRecovery.tsx",
  "src/components/UpdateDialog.tsx",
  "src/contexts/OnboardingContext.tsx",
  "src/contexts/TranscriptContext.tsx",
  "src/hooks/meeting-details/useCopyOperations.ts",
  "src/hooks/meeting-details/useMeetingData.ts",
  "src/hooks/meeting-details/useMeetingOperations.ts",
  "src/hooks/meeting-details/useModelConfiguration.ts",
  "src/hooks/meeting-details/useSummaryGeneration.ts",
  "src/hooks/useAudioPlayer.ts",
  "src/hooks/useAutoScroll.ts",
  "src/hooks/useImportAudio.ts",
  "src/hooks/useRecordingStart.ts",
  "src/hooks/useRecordingStateSync.ts",
  "src/hooks/useRecordingStop.ts",
  "src/hooks/useTranscriptRecovery.ts",
  "src/lib/analytics.ts",
  "src/services/storageService.ts",
  "src/types/index.ts",
  "tests/lib/blocknote-markdown.test.ts",
];

const eslintConfig = [
  {
    ignores: [
      ".next/**",
      "out/**",
      "node_modules/**",
      "coverage/**",
      "src-tauri/target/**",
    ],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    files: legacyAnyFiles,
    rules: {
      "@typescript-eslint/no-explicit-any": "warn",
    },
  },
];

export default eslintConfig;
