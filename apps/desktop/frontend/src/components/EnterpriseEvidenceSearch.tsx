'use client';

import { useEffect, useState } from 'react';
import { invoke } from '@tauri-apps/api/core';
import { ExternalLink, Search, ShieldAlert } from 'lucide-react';

import { listContextConnectors, searchEnterpriseEvidence } from '@/services/contextConnectorService';
import type {
  ConnectorHealth,
  ConnectorKind,
  EnterpriseSearchResult,
} from '@/services/intelligenceContracts';

const EMPTY_RESULT: EnterpriseSearchResult = {
  status: 'unavailable',
  citations: [],
  unavailable_connector_ids: [],
  expired_lease_connector_ids: [],
  elapsed_ms: 0,
};

const REASON_COPY: Record<string, string> = {
  lexical_match: 'matched your words',
  source_acl: 'authorized source',
  exact_business_key: 'exact business reference',
  source_authority: 'preferred source',
  freshness: 'recent revision',
  entity_match: 'matched system or entity',
};

export function EnterpriseEvidenceSearch() {
  const [query, setQuery] = useState('');
  const [connectors, setConnectors] = useState<ConnectorHealth[]>([]);
  const [selectedKinds, setSelectedKinds] = useState<ConnectorKind[]>([]);
  const [result, setResult] = useState<EnterpriseSearchResult>(EMPTY_RESULT);
  const [phase, setPhase] = useState<'idle' | 'searching' | 'ready' | 'error'>('idle');

  useEffect(() => {
    void listContextConnectors().then(setConnectors).catch(() => setConnectors([]));
  }, []);

  const kinds = Array.from(new Set(connectors.map((connector) => connector.kind)));

  const submit = async () => {
    if (!query.trim()) return;
    setPhase('searching');
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10_500);
    try {
      const next = await searchEnterpriseEvidence({
        text: query.trim(),
        connector_ids: [],
        source_kinds: selectedKinds,
        source_selection_ids: [],
        entity_types: [],
        limit: 20,
        deadline_ms: 10_000,
      }, controller.signal);
      setResult(next);
      setPhase('ready');
    } catch {
      setResult(EMPTY_RESULT);
      setPhase('error');
    } finally {
      window.clearTimeout(timeout);
    }
  };

  return (
    <section className="rounded-2xl border border-amber-200 bg-[#fffdf6] p-5" aria-labelledby="enterprise-search-heading">
      <div className="flex items-start gap-3">
        <Search className="mt-0.5 h-5 w-5 text-amber-900" aria-hidden="true" />
        <div>
          <h2 id="enterprise-search-heading" className="font-semibold text-slate-950">Global evidence search</h2>
          <p className="mt-1 text-sm text-slate-600">Search selected local and company sources in English or Japanese. Results are supplemental and are not meeting evidence.</p>
        </div>
      </div>
      <form className="mt-4 flex flex-col gap-3 sm:flex-row" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <label className="sr-only" htmlFor="enterprise-evidence-query">Search enterprise evidence</label>
        <input id="enterprise-evidence-query" value={query} onChange={(event) => setQuery(event.target.value)} maxLength={2000} placeholder="Order currency validation / 注文通貨の検証" className="min-w-0 flex-1 rounded-xl border border-amber-300 bg-white px-4 py-3 text-sm focus:border-amber-700 focus:outline-none focus:ring-2 focus:ring-amber-200" />
        <button type="submit" disabled={phase === 'searching' || !query.trim()} className="rounded-xl bg-amber-950 px-5 py-3 text-sm font-semibold text-white disabled:opacity-50">{phase === 'searching' ? 'Searching…' : 'Search evidence'}</button>
      </form>
      {kinds.length > 0 && <fieldset className="mt-3 flex flex-wrap gap-2">
        <legend className="sr-only">Filter source families</legend>
        {kinds.map((kind) => <label key={kind} className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-amber-200 bg-white px-3 py-1.5 text-xs text-slate-700"><input type="checkbox" checked={selectedKinds.includes(kind)} onChange={(event) => setSelectedKinds((current) => event.target.checked ? [...current, kind] : current.filter((value) => value !== kind))} className="accent-amber-900" />{kind.replaceAll('_', ' ')}</label>)}
      </fieldset>}
      <div className="mt-3 min-h-5 text-sm text-slate-600" role="status" aria-live="polite">
        {phase === 'error' && 'Search is unavailable. Recording and live questions are unaffected.'}
        {phase === 'ready' && result.status === 'timeout' && 'Search timed out. Narrow the sources or try again.'}
        {phase === 'ready' && result.expired_lease_connector_ids.length > 0 && 'Some sources require authorization renewal.'}
        {phase === 'ready' && result.status === 'current' && `${result.citations.length} results in ${result.elapsed_ms} ms.`}
        {phase === 'ready' && result.status === 'partial' && `${result.citations.length} results; some sources are temporarily unavailable.`}
      </div>
      {result.citations.length > 0 && <ol className="mt-3 space-y-3">
        {result.citations.map((citation) => <li key={citation.citation_id} className="rounded-xl border border-amber-100 bg-white p-4 shadow-[0_1px_0_rgba(120,53,15,0.06)]">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-semibold text-slate-950">{citation.source_label} · {citation.source_reference}</p>
            <span className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-medium text-slate-600">{citation.freshness} · {citation.source_kind.replaceAll('_', ' ')}</span>
          </div>
          <p className="mt-2 line-clamp-4 whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-700">{citation.excerpt}</p>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <span>Not stated in this meeting</span>
            <span aria-hidden="true">·</span>
            <span>{citation.local_indexed ? 'Encrypted local index' : 'Live remote result'}</span>
            {citation.score_reasons.length > 0 && <><span aria-hidden="true">·</span><span>Why: {citation.score_reasons.map((reason) => REASON_COPY[reason] ?? 'relevant evidence').join(', ')}</span></>}
          </div>
          {citation.uri && <button type="button" onClick={() => void invoke('open_external_url', { url: citation.uri })} className="mt-3 inline-flex items-center gap-1.5 text-xs font-semibold text-amber-900 underline-offset-4 hover:underline">Open confirmed source host <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" /></button>}
        </li>)}
      </ol>}
      {phase === 'ready' && result.citations.length === 0 && result.status !== 'timeout' && <div className="mt-3 flex items-center gap-2 rounded-lg border border-slate-200 bg-white p-3 text-sm text-slate-600"><ShieldAlert className="h-4 w-4" aria-hidden="true" />No authorized evidence matched this search.</div>}
    </section>
  );
}
