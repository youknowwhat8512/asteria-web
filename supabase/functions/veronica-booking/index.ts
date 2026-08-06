// Veronica charter booking — single router Edge Function.
//
// One deployable function handles every route (availability, login/logout,
// members, bookings CRUD, cancel). All request logic lives in the shared,
// dependency-injected router so it can be unit-tested without Deno. This file
// only wires the real service-role store, config, and calendar adapter.

import { loadConfig } from "../_shared/config.ts";
import { createCalendarAdapter } from "../_shared/calendar.ts";
import { handleRequest } from "../_shared/router.ts";
import { SupabaseStore } from "../_shared/supabase-store.ts";

// deno-lint-ignore no-explicit-any
declare const Deno: any;

const config = loadConfig();
const store = new SupabaseStore(config.supabaseUrl, config.serviceRoleKey);
const calendar = createCalendarAdapter(config.calendar);

Deno.serve((req: Request) =>
  handleRequest(req, {
    store,
    config,
    calendar,
    now: () => new Date(),
  })
);
