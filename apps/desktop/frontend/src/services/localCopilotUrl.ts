const DEFAULT_LOCAL_COPILOT_URL = "http://127.0.0.1:8000";

function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.replace(/^\[|\]$/g, "").toLowerCase();
  return normalized === "localhost" || normalized === "127.0.0.1" || normalized === "::1";
}

/**
 * Keep the IntelligencePanel on the local copilot backend even when a runtime
 * window override is present. Invalid values deliberately fall back to the
 * known loopback default instead of becoming an external fetch/WS target.
 */
export function localCopilotUrl(candidate: unknown): string {
  if (typeof candidate !== "string" || candidate.trim() === "") {
    return DEFAULT_LOCAL_COPILOT_URL;
  }

  try {
    const parsed = new URL(candidate.trim());
    const rootPath = parsed.pathname === "" || parsed.pathname === "/";
    if (
      parsed.protocol !== "http:" ||
      !isLoopbackHostname(parsed.hostname) ||
      parsed.username !== "" ||
      parsed.password !== "" ||
      parsed.search !== "" ||
      parsed.hash !== "" ||
      !rootPath
    ) {
      return DEFAULT_LOCAL_COPILOT_URL;
    }
    return parsed.origin;
  } catch {
    return DEFAULT_LOCAL_COPILOT_URL;
  }
}

export { DEFAULT_LOCAL_COPILOT_URL };
