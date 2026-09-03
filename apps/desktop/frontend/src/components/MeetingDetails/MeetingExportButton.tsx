"use client";

import { useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { Download } from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';

export function MeetingExportButton({ meetingId }: { meetingId: string }) {
  const [exporting, setExporting] = useState(false);

  const exportMeeting = async () => {
    if (exporting) return;
    setExporting(true);
    try {
      const saved = await invoke<boolean>('export_local_meeting', { meetingId });
      if (saved) {
        toast.success('Meeting exported', {
          description: 'The local JSON export contains meeting content but no internal IDs or paths.',
        });
      }
    } catch {
      toast.error('Meeting export is unavailable', {
        description: 'The saved meeting was not changed. Choose export again to retry.',
      });
    } finally {
      setExporting(false);
    }
  };

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      disabled={exporting}
      onClick={() => void exportMeeting()}
      aria-label="Export this meeting"
    >
      <Download size={16} aria-hidden="true" />
      <span>{exporting ? 'Exporting...' : 'Export meeting'}</span>
    </Button>
  );
}
