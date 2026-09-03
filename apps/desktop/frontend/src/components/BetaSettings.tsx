"use client"

import { useEffect, useState } from "react"
import { invoke } from "@tauri-apps/api/core"
import { Switch } from "./ui/switch"
import { FlaskConical, AlertCircle } from "lucide-react"
import { useConfig } from "@/contexts/ConfigContext"
import {
  BetaFeatureKey,
  BETA_FEATURE_NAMES,
  BETA_FEATURE_DESCRIPTIONS
} from "@/types/betaFeatures"
import {
  getPilotSettings,
  savePilotSettings,
  type PilotSettings,
} from "@/services/intelligencePilot"

export function BetaSettings() {
  const { betaFeatures, toggleBetaFeature } = useConfig();
  const [pilotSettings, setPilotSettings] = useState<PilotSettings | null>(null);
  const [semanticStatus, setSemanticStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');

  // Define feature order for display (allows custom ordering)
  const featureOrder: BetaFeatureKey[] = ['importAndRetranscribe'];

  useEffect(() => {
    void getPilotSettings().then(setPilotSettings).catch(() => setSemanticStatus('error'));
  }, []);

  const toggleSemanticBeta = async (enabled: boolean) => {
    if (!pilotSettings) return;
    setSemanticStatus('saving');
    try {
      const saved = await savePilotSettings({
        ...pilotSettings,
        semanticBetaEnabled: enabled,
        semanticModel: 'qwen3:8b',
      });
      setPilotSettings(saved);
      await invoke<boolean>('restart_meeting_intelligence_backend_for_configuration');
      setSemanticStatus('saved');
    } catch {
      setSemanticStatus('error');
    }
  };

  return (
    <div className="space-y-6">
      {/* Yellow Warning Banner */}
      <div className="flex items-start gap-3 p-4 bg-yellow-50 border border-yellow-200 rounded-lg">
        <AlertCircle className="h-5 w-5 text-yellow-600 flex-shrink-0 mt-0.5" />
        <div className="text-sm text-yellow-800">
          <p className="font-medium">Beta Features</p>
          <p className="mt-1">
            These features are still being tested. You may encounter issues, and we appreciate your feedback.
          </p>
        </div>
      </div>

      <div className="rounded-lg border border-amber-200 bg-white p-6 shadow-sm">
        <div className="flex items-center justify-between gap-6">
          <div className="min-w-0 flex-1">
            <div className="mb-2 flex items-center gap-2">
              <FlaskConical className="h-5 w-5 text-amber-700" />
              <h3 className="text-lg font-semibold text-gray-900">Local semantic problem hints</h3>
              <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">BETA</span>
            </div>
            <p className="text-sm leading-relaxed text-gray-600">
              Uses your existing local Ollama installation with qwen3:8b. Deterministic ASK NOW
              never waits for it, and a hint changes meeting state only after you confirm it.
              Meeting Intelligence Copilot does not download the model automatically.
            </p>
            <p className="mt-2 text-xs text-gray-500" role="status">
              {semanticStatus === 'saving' && 'Saving and restarting the local intelligence backend…'}
              {semanticStatus === 'saved' && 'Setting saved. The managed backend is restarting.'}
              {semanticStatus === 'error' && 'The semantic beta setting could not be changed.'}
            </p>
          </div>
          <Switch
            checked={pilotSettings?.semanticBetaEnabled ?? false}
            disabled={!pilotSettings || semanticStatus === 'saving'}
            onCheckedChange={(checked) => void toggleSemanticBeta(checked)}
            aria-label="Enable local semantic problem hints"
          />
        </div>
      </div>

      {/* Dynamic Feature Toggles - Automatically renders all features */}
      {featureOrder.map((featureKey) => (
        <div
          key={featureKey}
          className="bg-white rounded-lg border border-gray-200 p-6 shadow-sm"
        >
          <div className="flex items-center justify-between">
            <div className="flex-1">
              <div className="flex items-center gap-2 mb-2">
                <FlaskConical className="h-5 w-5 text-gray-600" />
                <h3 className="text-lg font-semibold text-gray-900">
                  {BETA_FEATURE_NAMES[featureKey]}
                </h3>
                <span className="px-2 py-0.5 text-xs font-medium bg-yellow-100 text-yellow-800 rounded-full">
                  BETA
                </span>
              </div>
              <p className="text-sm text-gray-600">
                {BETA_FEATURE_DESCRIPTIONS[featureKey]}
              </p>
            </div>

            <div className="ml-6">
              <Switch
                checked={betaFeatures[featureKey]}
                onCheckedChange={(checked) => toggleBetaFeature(featureKey, checked)}
              />
            </div>
          </div>
        </div>
      ))}

      {/* Info Box */}
      <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg">
        <p className="text-sm text-blue-800">
          <strong>Note:</strong> When disabled, beta features will be hidden. Your existing meetings remain unaffected.
        </p>
      </div>
    </div>
  );
}
