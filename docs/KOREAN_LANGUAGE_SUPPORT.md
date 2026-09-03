# Korean Language Support

Korean (`ko`) is a first-class explicit transcript language alongside Japanese (`ja`) and English
(`en`). The source configuration supplies the language; the adapter and analyzer never infer it from
Hangul or other Unicode ranges.

## Supported path

- Meetily sends `lang: "ko"` through the authenticated live-ingest and replay paths.
- Korean recording uses the multilingual local Whisper provider. Parakeet remains English-only.
- The deterministic analyzer covers all ten current problem templates and renders Korean ASK NOW and
  FOLLOW UP questions.
- Korean governance candidates retain transcript evidence IDs and still require explicit review.
- Local OCR supports the Tesseract `kor` language pack when
  `MEETING_INTELLIGENCE_OCR_LANGUAGES=jpn+eng+kor` is configured. It is not mandatory for existing
  JA/EN installations.
- The compact live panel, preflight, readiness flow, issue drafts, and exports accept Korean.

## Safety boundaries

- Korean does not alter `MeetingState`, evidence provenance, supersession, contradiction, or gap
  reopening rules.
- A Korean fact or pain point must cite real transcript event IDs.
- Connected sources remain supplemental and cannot become meeting evidence or fill a gap.
- No cloud speech or language provider is introduced.

## Current acceptance status

Deterministic Korean text tests and a Korean golden trajectory cover the source path. Real Korean
audio through Meetily, acoustic variation, natural conversation timing, and consented human review
remain release gates. The UI must not describe Korean as human-validated until those gates pass.

Run the standard verification entry point:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check.ps1
```

Meetily additionally requires its frontend and Rust checks from the canonical worktree.
