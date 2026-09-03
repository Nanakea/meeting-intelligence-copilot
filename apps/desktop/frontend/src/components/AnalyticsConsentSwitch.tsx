import { Info, ShieldCheck } from 'lucide-react';

export default function AnalyticsConsentSwitch() {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="mb-2 text-base font-semibold text-gray-800">Usage diagnostics</h3>
        <p className="text-sm text-gray-600">
          Remote analytics is not included in this local-first build.
        </p>
      </div>

      <div className="flex items-start gap-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3">
        <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-emerald-700" />
        <div>
          <h4 className="font-semibold text-emerald-900">No remote telemetry</h4>
          <p className="mt-1 text-sm text-emerald-800">
            Meeting content and usage events are not sent to an analytics service. Aggregate
            diagnostics are exported only when you explicitly request them.
          </p>
        </div>
      </div>

      <div className="flex items-start gap-2 rounded border border-blue-200 bg-blue-50 p-2">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" />
        <p className="text-xs text-blue-700">
          Aggregate exports exclude transcripts, audio, titles, speaker names, tokens,
          exceptions, and local paths.
        </p>
      </div>
    </div>
  );
}
