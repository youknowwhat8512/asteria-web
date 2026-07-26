// Veronica charter booking — production client.
//
// Security notes:
//  * The session lives in an HttpOnly cookie the API sets; JS never reads or
//    stores it. We send it with `credentials: "include"`.
//  * The CSRF token returned by /login is kept ONLY in a module-scoped variable
//    (never in web storage or the URL) and sent as the X-CSRF-Token header on
//    mutations.
//  * All dynamic content is inserted via textContent / createElement (never by
//    assigning raw markup) so untrusted strings can't inject.

(() => {
  "use strict";

  const API_BASE = (document.querySelector('meta[name="veronica-api-base"]')?.content || "").replace(/\/$/, "");
  const WAIVER_VERSION = "waiver-2026-07-draft";
  const PRIVACY_VERSION = "privacy-2026-07-draft";

  // In-memory only. Cleared on reload/logout.
  let csrfToken = null;
  let members = [];
  let editingId = null;
  let createIdempotencyKey = null;

  const $ = (id) => document.getElementById(id);
  const show = (elm) => elm.classList.remove("hidden");
  const hide = (elm) => elm.classList.add("hidden");

  function setError(id, message) {
    const e = $(id);
    if (!message) { e.classList.remove("show"); e.textContent = ""; return; }
    e.textContent = message;
    e.classList.add("show");
  }

  function busy(btn, on, label) {
    if (!btn) return;
    btn.disabled = on;
    if (on) { btn.dataset.label = btn.textContent; btn.textContent = label || "처리 중…"; }
    else if (btn.dataset.label) { btn.textContent = btn.dataset.label; }
  }

  // -------------------------------------------------------------------------
  // API client
  // -------------------------------------------------------------------------
  async function api(method, path, { body, csrf, idempotencyKey } = {}) {
    if (!API_BASE) throw new Error("api_base_missing");
    const headers = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (csrf && csrfToken) headers["X-CSRF-Token"] = csrfToken;
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    const res = await fetch(API_BASE + path, {
      method,
      credentials: "include",
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch { /* empty */ }
    if (!res.ok) {
      const err = new Error((data && data.error) || `http_${res.status}`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  const ERROR_TEXT = {
    invalid_credentials: "아이디 또는 비밀번호가 올바르지 않습니다.",
    too_many_attempts: "로그인 시도가 많습니다. 잠시 후 다시 시도하세요.",
    time_conflict: "선택한 시간에 이미 예약이 있습니다. 다른 시간을 선택하세요.",
    invariant_violation: "입력한 예약 정보가 조건을 만족하지 않습니다.",
    invalid_body: "입력값을 다시 확인해주세요.",
    csrf_failed: "보안 검증에 실패했습니다. 다시 로그인해주세요.",
    unauthorized: "로그인이 필요합니다.",
    not_found: "예약을 찾을 수 없습니다.",
    api_base_missing: "API 주소가 설정되지 않았습니다.",
  };
  const msg = (e) => ERROR_TEXT[e && e.message] || "요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.";

  // -------------------------------------------------------------------------
  // DOM helpers (safe construction)
  // -------------------------------------------------------------------------
  function el(tag, opts = {}, children = []) {
    const node = document.createElement(tag);
    if (opts.class) node.className = opts.class;
    if (opts.text != null) node.textContent = opts.text;
    if (opts.attrs) for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, v);
    for (const c of children) if (c) node.appendChild(c);
    return node;
  }

  function fmtRange(startIso, endIso) {
    const opts = { month: "long", day: "numeric", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Seoul" };
    const s = new Intl.DateTimeFormat("ko-KR", opts).format(new Date(startIso));
    const e = new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Seoul" }).format(new Date(endIso));
    return `${s} – ${e}`;
  }

  // -------------------------------------------------------------------------
  // Public availability
  // -------------------------------------------------------------------------
  async function loadAvailability() {
    const from = $("availFrom").value;
    const to = $("availTo").value;
    setError("availError", "");
    if (!from || !to) { setError("availError", "시작일과 종료일을 선택하세요."); return; }
    const result = $("availResult");
    result.replaceChildren(el("div", { class: "state", text: "불러오는 중…" }));
    try {
      const fromIso = new Date(`${from}T00:00:00+09:00`).toISOString();
      const toIso = new Date(`${to}T23:59:59+09:00`).toISOString();
      const data = await api("GET", `/availability?from=${encodeURIComponent(fromIso)}&to=${encodeURIComponent(toIso)}`);
      renderAvailability(data);
    } catch (e) {
      result.replaceChildren();
      setError("availError", msg(e));
    }
  }

  function renderAvailability(data) {
    const result = $("availResult");
    const items = [];
    for (const b of data.bookings || []) {
      items.push(el("div", { class: "item" }, [
        el("div", { class: "when", text: fmtRange(b.startsAt, b.endsAt) }),
        el("div", { class: "meta", text: "예약됨" }),
      ]));
    }
    for (const blk of data.blocks || []) {
      const row = el("div", { class: "item" }, [
        el("div", { class: "when", text: fmtRange(blk.startsAt, blk.endsAt) }),
        el("div", { class: "meta" }, [el("span", { class: "tag block", text: blk.label || "[예약불가]" })]),
      ]);
      items.push(row);
    }
    if (!items.length) {
      result.replaceChildren(el("div", { class: "state", text: "이 기간에는 등록된 예약이 없습니다. 예약 가능합니다." }));
      return;
    }
    const note = data.calendarAvailable ? null
      : el("p", { class: "meta", text: "· 정비 캘린더 연동은 아직 설정되지 않아 [예약불가] 일정은 표시되지 않습니다.", attrs: { style: "color:var(--muted);font-size:11px;margin-top:10px" } });
    result.replaceChildren(el("div", { class: "list" }, items), note);
  }

  // -------------------------------------------------------------------------
  // Auth
  // -------------------------------------------------------------------------
  async function doLogin() {
    setError("loginError", "");
    const id = $("loginId").value.trim();
    const password = $("loginPw").value;
    if (!password) { setError("loginError", "비밀번호를 입력하세요."); return; }
    busy($("loginBtn"), true, "로그인 중…");
    try {
      const data = await api("POST", "/login", { body: { id, password } });
      csrfToken = data.csrfToken;
      onLoggedIn();
    } catch (e) {
      setError("loginError", msg(e));
    } finally {
      busy($("loginBtn"), false);
    }
  }

  async function doLogout() {
    // Logout is a mutation: send the session-bound CSRF token like every other.
    try { await api("POST", "/logout", { csrf: true }); } catch { /* ignore */ }
    csrfToken = null;
    members = [];
    $("loginPw").value = "";
    hide($("memberArea"));
    show($("loginSection"));
    hide($("logoutBtn"));
    $("sessionState").textContent = "비로그인";
  }

  async function onLoggedIn() {
    hide($("loginSection"));
    show($("memberArea"));
    show($("logoutBtn"));
    $("sessionState").textContent = "로그인됨";
    try {
      const data = await api("GET", "/members");
      members = data.members || [];
    } catch (e) {
      members = [];
    }
    showList();
  }

  async function resumeSession() {
    try {
      const data = await api("GET", "/session");
      csrfToken = data.csrfToken;
      await onLoggedIn();
    } catch (e) {
      if (e.status !== 401) setError("loginError", msg(e));
    }
  }

  // -------------------------------------------------------------------------
  // Booking list + detail
  // -------------------------------------------------------------------------
  function switchView(view) {
    hide($("bookingListSection"));
    hide($("bookingDetailSection"));
    hide($("bookingFormSection"));
    if (view) show(view);
  }

  async function showList() {
    switchView($("bookingListSection"));
    const result = $("bookingListResult");
    result.replaceChildren(el("div", { class: "state", text: "불러오는 중…" }));
    try {
      const data = await api("GET", "/bookings");
      const bookings = data.bookings || [];
      if (!bookings.length) {
        result.replaceChildren(el("div", { class: "state", text: "등록된 예약이 없습니다." }));
        return;
      }
      const list = el("div", { class: "list" });
      for (const b of bookings) {
        const tag = el("span", { class: `tag ${b.status === "cancelled" ? "cancelled" : "confirmed"}`, text: b.status === "cancelled" ? "취소됨" : "확정" });
        const item = el("div", { class: "item" }, [
          el("div", { class: "when", text: fmtRange(b.startsAt, b.endsAt) }),
          el("div", { class: "meta", text: `${b.bookerName} · ${b.departure} → ${b.destination} · ${b.totalPeople}명` }),
          el("div", {}, [tag]),
        ]);
        item.addEventListener("click", () => showDetail(b.id));
        list.appendChild(item);
      }
      result.replaceChildren(list);
    } catch (e) {
      result.replaceChildren(el("div", { class: "state", text: msg(e) }));
    }
  }

  function detailRow(label, value) {
    return el("div", { class: "detail-row" }, [el("span", { text: label }), el("b", { text: value })]);
  }

  let currentDetail = null;
  async function showDetail(id) {
    switchView($("bookingDetailSection"));
    setError("detailError", "");
    const result = $("bookingDetailResult");
    result.replaceChildren(el("div", { class: "state", text: "불러오는 중…" }));
    try {
      const data = await api("GET", `/bookings/${encodeURIComponent(id)}`);
      const b = data.booking;
      currentDetail = b;
      const rows = [
        detailRow("예약번호", b.bookingCode),
        detailRow("상태", b.status === "cancelled" ? "취소됨" : "확정"),
        detailRow("운항", fmtRange(b.startsAt, b.endsAt)),
        detailRow("항로", `${b.departure} → ${b.destination}`),
        detailRow("예약자", b.bookerName),
        detailRow("연락처", b.bookerPhone),
        detailRow("총 인원", `${b.totalPeople}명`),
      ];
      const paxNodes = (b.passengers || []).map((p) =>
        el("div", { class: "detail-row" }, [
          el("span", { text: p.type === "member" ? "회원" : "비회원" }),
          el("b", { text: p.type === "member" ? (p.name || "회원") : `${p.name} · ${p.phone || ""}` }),
        ])
      );
      result.replaceChildren(el("div", {}, rows), el("h2", { text: "탑승자" }), el("div", {}, paxNodes));
      // cancelled bookings can't be cancelled/edited again
      $("detailCancelBtn").disabled = b.status === "cancelled";
      $("detailEditBtn").disabled = b.status === "cancelled";
    } catch (e) {
      result.replaceChildren(el("div", { class: "state", text: msg(e) }));
    }
  }

  async function cancelCurrent() {
    if (!currentDetail) return;
    if (!confirm("이 예약을 취소하시겠습니까?")) return;
    setError("detailError", "");
    busy($("detailCancelBtn"), true, "취소 중…");
    try {
      await api("POST", `/bookings/${encodeURIComponent(currentDetail.id)}/cancel`, { csrf: true });
      await showDetail(currentDetail.id);
    } catch (e) {
      setError("detailError", msg(e));
    } finally {
      busy($("detailCancelBtn"), false);
    }
  }

  // -------------------------------------------------------------------------
  // Booking form (create + edit)
  // -------------------------------------------------------------------------
  function fillHourOptions() {
    for (const selId of ["startHour", "endHour"]) {
      const sel = $(selId);
      sel.replaceChildren();
      for (let h = 0; h <= 24; h++) {
        if (selId === "startHour" && h === 24) continue;
        if (selId === "endHour" && h === 0) continue;
        sel.appendChild(el("option", { text: `${String(h).padStart(2, "0")}:00`, attrs: { value: String(h) } }));
      }
    }
    $("startHour").value = "9";
    $("endHour").value = "12";
  }

  function fillMemberSelect(sel, selectedId) {
    sel.replaceChildren(el("option", { text: "회원 선택", attrs: { value: "" } }));
    for (const m of members) {
      const o = el("option", { text: m.displayName, attrs: { value: m.id } });
      if (m.id === selectedId) o.selected = true;
      sel.appendChild(o);
    }
  }

  function addPaxRow(type, preset = {}) {
    const list = $("paxList");
    const row = el("div", { class: "pax-row" });
    const kind = el("div", { text: type === "member" ? "회원" : "비회원", attrs: { style: "font-size:12px;color:var(--muted)" } });
    row.dataset.type = type;
    if (type === "member") {
      const sel = el("select");
      fillMemberSelect(sel, preset.memberId);
      row.append(kind, sel, el("div"), removeBtn(row));
    } else {
      const name = el("input", { attrs: { placeholder: "성명" } });
      if (preset.name) name.value = preset.name;
      const phone = el("input", { attrs: { placeholder: "010-0000-0000", inputmode: "numeric" } });
      if (preset.phone) phone.value = preset.phone;
      row.append(kind, name, phone, removeBtn(row));
    }
    list.appendChild(row);
    updatePaxCount();
  }

  function removeBtn(row) {
    const b = el("button", { class: "x", text: "✕", attrs: { type: "button", "aria-label": "탑승자 삭제" } });
    b.addEventListener("click", () => { row.remove(); updatePaxCount(); });
    return b;
  }

  function updatePaxCount() {
    const n = $("paxList").querySelectorAll(".pax-row").length;
    $("paxCount").textContent = `현재 탑승자 ${n}명 — 총 승선 인원과 정확히 일치해야 합니다.`;
  }

  function collectPassengers() {
    const rows = [...$("paxList").querySelectorAll(".pax-row")];
    const pax = [];
    for (const row of rows) {
      if (row.dataset.type === "member") {
        const memberId = row.querySelector("select").value;
        if (!memberId) return { error: "회원 탑승자를 선택하세요." };
        pax.push({ type: "member", memberId });
      } else {
        const inputs = row.querySelectorAll("input");
        const name = inputs[0].value.trim();
        const phone = inputs[1].value.replace(/[^0-9]/g, "");
        if (!name || !/^01[0-9]{8,9}$/.test(phone)) return { error: "비회원 탑승자의 성명과 전화번호를 확인하세요." };
        pax.push({ type: "guest", name, phone });
      }
    }
    return { pax };
  }

  function openForm(prefill) {
    switchView($("bookingFormSection"));
    setError("formError", "");
    fillHourOptions();
    fillMemberSelect($("responsibleMember"), prefill ? prefill.responsibleMemberId : "");
    $("paxList").replaceChildren();
    editingId = prefill ? prefill.id : null;
    createIdempotencyKey = prefill ? null : crypto.randomUUID();
    $("waiverAgree").checked = false;
    $("privacyAgree").checked = false;
    $("formTitle").textContent = prefill ? "예약 변경" : "새 예약 등록";
    $("formSubmitBtn").textContent = prefill ? "변경 저장" : "예약 확정";
    if (prefill) {
      const start = new Date(prefill.startsAt);
      const end = new Date(prefill.endsAt);
      const kstDate = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(start);
      $("bookDate").value = kstDate;
      $("startHour").value = String(Number(new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Seoul", hour: "2-digit", hour12: false }).format(start)) % 24);
      $("endHour").value = String(Number(new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Seoul", hour: "2-digit", hour12: false }).format(end)) % 24 || 24);
      $("bookerName").value = prefill.bookerName || "";
      $("bookerPhone").value = prefill.bookerPhone || "";
      $("destination").value = prefill.destination || "";
      for (const p of prefill.passengers || []) addPaxRow(p.type, p);
    } else {
      addPaxRow("member");
    }
    updatePaxCount();
  }

  function isoFromDateHour(dateStr, hour) {
    // KST whole-hour → UTC ISO (whole hour, since KST offset is a whole hour).
    return new Date(`${dateStr}T${String(hour).padStart(2, "0")}:00:00+09:00`).toISOString();
  }

  async function submitForm() {
    setError("formError", "");
    const date = $("bookDate").value;
    const startH = Number($("startHour").value);
    const endH = Number($("endHour").value);
    if (!date) return setError("formError", "운항일을 선택하세요.");
    if (endH <= startH) return setError("formError", "종료 시각은 시작 시각보다 뒤여야 합니다.");
    const responsibleMemberId = $("responsibleMember").value;
    if (!responsibleMemberId) return setError("formError", "책임 운항자를 선택하세요.");
    const bookerPhoneDigits = $("bookerPhone").value.replace(/[^0-9]/g, "");
    if (!/^01[0-9]{8,9}$/.test(bookerPhoneDigits)) return setError("formError", "예약자 전화번호를 확인하세요.");
    if (!$("destination").value.trim()) return setError("formError", "목적지를 입력하세요.");
    if (!$("bookerName").value.trim()) return setError("formError", "예약자 성명을 입력하세요.");
    if (!$("waiverAgree").checked) return setError("formError", "안전·파손 책임 서약에 동의해야 합니다.");
    if (!$("privacyAgree").checked) return setError("formError", "개인정보 수집 동의 확인이 필요합니다.");
    const collected = collectPassengers();
    if (collected.error) return setError("formError", collected.error);
    const passengers = collected.pax;
    if (!passengers.some((p) => p.type === "member")) return setError("formError", "회원 탑승자가 최소 1명 필요합니다.");

    const payload = {
      startsAt: isoFromDateHour(date, startH),
      endsAt: isoFromDateHour(date, endH),
      destination: $("destination").value.trim(),
      totalPeople: passengers.length,
      responsibleMemberId,
      bookerName: $("bookerName").value.trim(),
      bookerPhone: bookerPhoneDigits,
      passengers,
      waiverVersion: WAIVER_VERSION,
      waiverAccepted: true,
      privacyConsentVersion: PRIVACY_VERSION,
      privacyConsentAccepted: true,
    };

    busy($("formSubmitBtn"), true, "저장 중…");
    try {
      if (editingId) {
        await api("PATCH", `/bookings/${encodeURIComponent(editingId)}`, { body: payload, csrf: true });
        await showDetail(editingId);
      } else {
        const res = await api("POST", "/bookings", {
          body: payload,
          csrf: true,
          // Reuse this key after an ambiguous network failure. It is reset only
          // when a fresh create form opens or the server confirms success.
          idempotencyKey: createIdempotencyKey,
        });
        createIdempotencyKey = null;
        await showDetail(res.id);
      }
    } catch (e) {
      setError("formError", msg(e));
    } finally {
      busy($("formSubmitBtn"), false);
    }
  }

  // -------------------------------------------------------------------------
  // Wiring
  // -------------------------------------------------------------------------
  function initDates() {
    const today = new Date();
    const iso = (d) => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
    const week = new Date(today.getTime() + 7 * 864e5);
    $("availFrom").value = iso(today);
    $("availTo").value = iso(week);
    $("bookDate").min = iso(today);
  }

  function bindPhoneFormatting(input) {
    input.addEventListener("input", (ev) => {
      const d = ev.target.value.replace(/\D/g, "").slice(0, 11);
      ev.target.value = d.length > 7 ? `${d.slice(0, 3)}-${d.slice(3, 7)}-${d.slice(7)}`
        : d.length > 3 ? `${d.slice(0, 3)}-${d.slice(3)}` : d;
    });
  }

  function init() {
    if (!API_BASE) {
      setError("configError", "API 주소가 설정되지 않았습니다. 운영자가 veronica-api-base 메타 값을 설정해야 합니다.");
      $("availBtn").disabled = true;
      $("loginBtn").disabled = true;
    }
    initDates();
    bindPhoneFormatting($("bookerPhone"));
    $("availBtn").addEventListener("click", loadAvailability);
    $("loginBtn").addEventListener("click", doLogin);
    $("loginPw").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
    $("logoutBtn").addEventListener("click", doLogout);
    $("showListBtn").addEventListener("click", showList);
    $("showNewBtn").addEventListener("click", () => openForm(null));
    $("detailBackBtn").addEventListener("click", showList);
    $("detailCancelBtn").addEventListener("click", cancelCurrent);
    $("detailEditBtn").addEventListener("click", () => currentDetail && openForm(currentDetail));
    $("formCancelBtn").addEventListener("click", showList);
    $("formSubmitBtn").addEventListener("click", submitForm);
    $("addMemberPaxBtn").addEventListener("click", () => addPaxRow("member"));
    $("addGuestPaxBtn").addEventListener("click", () => addPaxRow("guest"));
    if (API_BASE) resumeSession();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
