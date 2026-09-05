import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WorkdayReadinessCard } from '@/components/WorkdayReadinessCard';
import { getPilotPreflight } from '@/services/intelligencePilot';

vi.mock('@/services/intelligencePilot', () => ({
  getPilotPreflight: vi.fn(),
  runSpokenPipelinePreflight: vi.fn(),
}));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

const ready = {
  language: 'en', languageSupported: true, localSttReady: true,
  microphoneAvailable: true, systemAudioAvailable: true,
  backend: { phase: 'ready' }, availableDiskBytes: 4 * 1024 ** 3,
  pilotDataAvailableDiskBytes: 4 * 1024 ** 3, retention: 'forever',
} as Awaited<ReturnType<typeof getPilotPreflight>>;

describe('workday preflight', () => {
  it('does not display an old English success after changing to Korean', async () => {
    let resolve!: (value: typeof ready) => void;
    vi.mocked(getPilotPreflight).mockImplementation(() => new Promise((done) => { resolve = done; }));
    render(<WorkdayReadinessCard microphoneDevice={null} systemAudioDevice={null} />);
    fireEvent.click(screen.getByRole('button', { name: 'Check local setup' }));
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'ko' } });
    await act(async () => { resolve(ready); });
    expect(screen.queryByText('로컬 회의 준비 완료')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '전사부터 패널까지 테스트' })).toBeDisabled();
  });

  it('clears readiness when a capture device changes', async () => {
    vi.mocked(getPilotPreflight).mockResolvedValue(ready);
    const view = render(<WorkdayReadinessCard microphoneDevice="one" systemAudioDevice={null} />);
    fireEvent.click(screen.getByRole('button', { name: 'Check local setup' }));
    await screen.findByText('Ready for a local meeting');
    view.rerender(<WorkdayReadinessCard microphoneDevice="two" systemAudioDevice={null} />);
    expect(screen.queryByText('Ready for a local meeting')).not.toBeInTheDocument();
  });

  it('uses fixed safe error copy without exposing exception content', async () => {
    vi.mocked(getPilotPreflight).mockRejectedValue(new Error('PRIVATE_TOKEN C:\\private'));
    render(<WorkdayReadinessCard microphoneDevice={null} systemAudioDevice={null} />);
    fireEvent.click(screen.getByRole('button', { name: 'Check local setup' }));
    await waitFor(() => expect(screen.getByText(/readiness check could not complete/)).toBeInTheDocument());
    expect(document.body.textContent).not.toContain('PRIVATE_TOKEN');
    expect(document.body.textContent).not.toContain('C:\\private');
  });

  it('warns about unavailable intelligence without blocking local recording', async () => {
    vi.mocked(getPilotPreflight).mockResolvedValue({ ...ready, backend: { ...ready.backend, phase: 'unavailable' } });
    render(<WorkdayReadinessCard microphoneDevice={null} systemAudioDevice={null} />);
    fireEvent.click(screen.getByRole('button', { name: 'Check local setup' }));
    await screen.findByText(/Local recording is ready/);
    expect(screen.getByRole('button', { name: 'Test transcript to panel' })).toBeDisabled();
  });
});
