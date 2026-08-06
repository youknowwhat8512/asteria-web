// Google Calendar adapter.
//
// IMPORTANT: this integration is NOT live. Per the plan, Calendar is
// represented by a hardened adapter interface and an explicit "unconfigured"
// state. Availability must fail safely when Calendar is unconfigured, exposing
// no blocks rather than erroring. We never fabricate calendar success.

export interface CalendarBlock {
  startsAt: string;
  endsAt: string;
  label: string; // "[예약불가]"
}

export interface CalendarListResult {
  available: boolean; // false → integration not configured / unreachable
  blocks: CalendarBlock[];
}

export interface CalendarAdapter {
  isConfigured(): boolean;
  /** Fetch [예약불가] maintenance blocks. Must never throw; returns available=false on any issue. */
  listBlocks(from: string, to: string): Promise<CalendarListResult>;
}

/** The only adapter we ship today: explicitly unconfigured, always safe. */
export class UnconfiguredCalendarAdapter implements CalendarAdapter {
  isConfigured(): boolean {
    return false;
  }
  // deno-lint-ignore require-await
  async listBlocks(_from: string, _to: string): Promise<CalendarListResult> {
    return { available: false, blocks: [] };
  }
}

export interface CalendarConfig {
  calendarId?: string;
  oauthClientId?: string;
  oauthClientSecret?: string;
  oauthRefreshToken?: string;
}

/**
 * Factory. Returns the unconfigured adapter unless a COMPLETE set of Calendar
 * secrets is present. Even when secrets are present we deliberately return the
 * unconfigured adapter until a reviewed live implementation lands — this keeps
 * us from ever claiming the integration is live. Callers get a clear
 * available=false signal and degrade gracefully.
 */
export function createCalendarAdapter(_config: CalendarConfig): CalendarAdapter {
  return new UnconfiguredCalendarAdapter();
}
