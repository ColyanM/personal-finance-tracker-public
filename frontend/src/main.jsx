import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const PAGES = [
  { path: "/", label: "Dashboard" },
  { path: "/accounts", label: "Accounts" },
  { path: "/transactions", label: "Transactions" },
  { path: "/cash-flow", label: "Cash Flow" },
  { path: "/reports", label: "Reports" },
  { path: "/budgets", label: "Budget" },
  { path: "/recurring", label: "Recurring" },
  { path: "/goals", label: "Goals" },
  { path: "/investments", label: "Investments" },
  { path: "/rules", label: "Rules" },
  { path: "/settings", label: "Settings" },
  { path: "/sync", label: "Sync" },
];

const PAGE_COPY = {
  "/": "Your money at a glance",
  "/accounts": "Balances and net worth",
  "/transactions": "Review and manage spending",
  "/cash-flow": "Income, expenses, and savings",
  "/reports": "Longer-term spending totals",
  "/budgets": "Plan your monthly budget",
  "/recurring": "Expected payments and income",
  "/goals": "Track savings targets",
  "/investments": "Holdings grouped by ticker",
  "/rules": "Automatic category rules",
  "/sync": "Refresh and import status",
  "/settings": "Display and alert preferences",
};

const ICONS = {
  "/": (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </>
  ),
  "/accounts": (
    <>
      <path d="M4 8h16v11H4z" />
      <path d="M7 8V6h10v2" />
      <path d="M16 13h4" />
      <circle cx="16" cy="13" r="1" />
    </>
  ),
  "/transactions": (
    <>
      <rect x="3" y="6" width="18" height="12" rx="2" />
      <path d="M3 10h18" />
      <path d="M7 15h4" />
    </>
  ),
  "/cash-flow": (
    <>
      <path d="M5 19V9" />
      <path d="M12 19V5" />
      <path d="M19 19v-7" />
      <path d="M3 19h18" />
    </>
  ),
  "/reports": (
    <>
      <path d="M12 3v9h9" />
      <path d="M20.5 14.5A9 9 0 1 1 9.5 3.5" />
    </>
  ),
  "/budgets": (
    <>
      <rect x="4" y="5" width="16" height="16" rx="2" />
      <path d="M8 3v4" />
      <path d="M16 3v4" />
      <path d="M4 10h16" />
      <path d="M8 15h5" />
    </>
  ),
  "/recurring": (
    <>
      <path d="M17 2v5h-5" />
      <path d="M7 22v-5h5" />
      <path d="M18.8 9A7 7 0 0 0 7.2 5.2L3 9" />
      <path d="M5.2 15a7 7 0 0 0 11.6 3.8L21 15" />
    </>
  ),
  "/goals": (
    <>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="12" cy="12" r="1" />
    </>
  ),
  "/investments": (
    <>
      <path d="M4 17l5-5 4 4 7-9" />
      <path d="M15 7h5v5" />
      <path d="M4 21h16" />
    </>
  ),
  "/rules": (
    <>
      <path d="M4 6h10" />
      <path d="M4 12h16" />
      <path d="M4 18h8" />
      <circle cx="17" cy="6" r="2" />
      <circle cx="9" cy="18" r="2" />
    </>
  ),
  "/settings": (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.4 1a7 7 0 0 0-2-1.2L14 3h-4l-.5 2.7a7 7 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.4 2.4-1a7 7 0 0 0 2 1.2L10 21h4l.5-2.7a7 7 0 0 0 2-1.2l2.4 1 2-3.4-2-1.5c.1-.4.1-.8.1-1.2z" />
    </>
  ),
  "/sync": (
    <>
      <path d="M20 7h-7a4 4 0 0 0-4 4v1" />
      <path d="M17 4l3 3-3 3" />
      <path d="M4 17h7a4 4 0 0 0 4-4v-1" />
      <path d="M7 20l-3-3 3-3" />
    </>
  ),
};

const TRANSACTION_PAGE_SIZE = 150;
const MAX_TRANSACTION_LIMIT = 500;

const MONEY_PATHS = new Set([
  "/",
  "/accounts",
  "/transactions",
  "/cash-flow",
  "/reports",
  "/budgets",
  "/recurring",
  "/goals",
  "/investments",
]);

function titleFor(path) {
  return PAGES.find((page) => page.path === path)?.label || "Dashboard";
}

function cleanPath(path) {
  return PAGES.some((page) => page.path === path) ? path : "/";
}

function Icon({ path }) {
  return (
    <svg className="nav-svg" viewBox="0 0 24 24" aria-hidden="true">
      {ICONS[path] || ICONS["/"]}
    </svg>
  );
}

function BrandIcon() {
  return (
    <svg className="brand-svg" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M5 16V9a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v7a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3z" />
      <path d="M8 10h8" />
      <path d="M9 14h2" />
      <path d="M15 14h.1" />
    </svg>
  );
}

function moneyFromMinor(amountMinor, currency) {
  const number = Number(amountMinor || 0);
  const sign = number < 0 ? "-" : "";
  return `${sign}${moneyAmountOnly(number)} ${currency}`;
}

function moneyNoCurrency(amountMinor) {
  const number = Number(amountMinor || 0);
  const sign = number < 0 ? "-" : "";
  return `${sign}${moneyAmountOnly(number)}`;
}

function moneyAmountOnly(amountMinor) {
  const amount = Math.abs(Number(amountMinor || 0)) / 100;
  return `$${amount.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function compactMoneyAmount(amountMinor) {
  const number = Number(amountMinor || 0);
  const amount = Math.abs(number) / 100;
  if (amount < 100000) return moneyAmountOnly(number);
  const sign = number < 0 ? "-" : "";
  return `${sign}$${new Intl.NumberFormat(undefined, {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(amount)}`;
}

function smallestTickStep(contextValues = []) {
  const values = Array.from(new Set(contextValues.map(Number).filter(Number.isFinite))).sort((a, b) => a - b);
  let step = null;
  for (let index = 1; index < values.length; index += 1) {
    const difference = Math.abs(values[index] - values[index - 1]);
    if (difference > 0 && (step === null || difference < step)) step = difference;
  }
  return step ? step / 100 : 0;
}

function compactDecimal(value, decimals) {
  return value.toFixed(decimals).replace(/\.0+$/, "").replace(/(\.\d*[1-9])0+$/, "$1");
}

function chartAxisMoney(amountMinor, contextValues = []) {
  const number = Number(amountMinor || 0);
  const amount = Math.abs(number) / 100;
  const sign = number < 0 ? "-" : "";
  const tickStep = smallestTickStep(contextValues);

  if (amount < 0.5) return "$0";
  if (amount >= 1000000) {
    const decimals = tickStep < 1000 ? 4 : tickStep < 10000 ? 3 : tickStep < 100000 ? 2 : tickStep < 1000000 ? 1 : 0;
    return `${sign}$${(amount / 1000000).toFixed(decimals)}M`;
  }
  if (amount >= 1000) {
    const decimals = tickStep < 100 ? 2 : tickStep < 1000 ? 1 : 0;
    return `${sign}$${compactDecimal(amount / 1000, decimals)}K`;
  }

  return `${sign}$${amount.toFixed(0)}`;
}

function niceTickStep(rawStep) {
  if (!Number.isFinite(rawStep) || rawStep <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const normalized = rawStep / magnitude;
  if (normalized <= 1) return magnitude;
  if (normalized <= 2) return 2 * magnitude;
  if (normalized <= 5) return 5 * magnitude;
  return 10 * magnitude;
}

function niceTickValues(minValue, maxValue, count = 5) {
  if (!Number.isFinite(minValue) || !Number.isFinite(maxValue)) return [0, 1];
  if (minValue === maxValue) {
    const padding = Math.max(Math.abs(minValue) * 0.08, 100);
    minValue -= padding;
    maxValue += padding;
  }

  const step = niceTickStep((maxValue - minValue) / Math.max(count - 1, 1));
  const start = Math.floor(minValue / step) * step;
  const end = Math.ceil(maxValue / step) * step;
  const ticks = [];

  for (let value = start; value <= end + step / 2; value += step) {
    ticks.push(Math.round(value));
  }

  return ticks.length ? ticks : [start, end];
}

function chartAxisRange(values, count = 5) {
  const finiteValues = values.map(Number).filter(Number.isFinite);
  if (!finiteValues.length) return { lower: 0, upper: 1, ticks: [0, 1] };

  const min = Math.min(...finiteValues);
  const max = Math.max(...finiteValues);
  const spread = Math.max(max - min, 1);
  const padding = Math.max(spread * 0.08, Math.abs(max) * 0.002, 100);
  const ticks = niceTickValues(min - padding, max + padding, count);
  return {
    lower: ticks[0],
    upper: ticks[ticks.length - 1],
    ticks,
  };
}

function chartDateLabel(row, includeYear = false) {
  if (row.date) {
    const parsed = new Date(`${row.date}T00:00:00`);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.toLocaleDateString(undefined, {
        month: "short",
        day: includeYear ? undefined : "numeric",
        year: includeYear ? "numeric" : undefined,
      });
    }
  }

  return row.label || "";
}

function budgetPeriodEnd(startDate) {
  if (!startDate) return "";
  const parsed = new Date(`${startDate}T00:00:00`);
  return new Date(parsed.getFullYear(), parsed.getMonth() + 1, 0).toISOString().slice(0, 10);
}

function budgetTransactionPath(row, startDate) {
  const params = new URLSearchParams({ category: row.category });
  if (startDate) {
    params.set("start", startDate);
    params.set("end", budgetPeriodEnd(startDate));
  }
  return `/transactions?${params.toString()}`;
}

function transactionsPath({ category = "", start = "", end = "" }) {
  const params = new URLSearchParams();
  if (category) params.set("category", category);
  if (start && !start.startsWith("0001")) params.set("start", start);
  if (end) params.set("end", end);
  const query = params.toString();
  return query ? `/transactions?${query}` : "/transactions";
}

function chartDateRange(summary) {
  return {
    start: summary?.start_date || "",
    end: summary?.end_date || "",
  };
}

function moneyInputFromMinor(amountMinor) {
  return (Math.abs(Number(amountMinor || 0)) / 100).toFixed(2);
}

function percent(part, total) {
  if (!total) return "0%";
  return `${((Math.abs(part) / Math.abs(total)) * 100).toFixed(1)}%`;
}

function valueTone(value) {
  const number = Number(value || 0);
  if (number > 0) return "positive";
  if (number < 0) return "negative";
  return "";
}

async function apiGet(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "Request failed");
  }
  return data;
}

async function apiPost(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || "The change could not be saved");
  }
  return data;
}

function useApi(path, fallback, options = {}) {
  const keepPreviousData = Boolean(options.keepPreviousData);
  const [state, setState] = useState({ loading: true, error: "", data: fallback, hasLoaded: false });

  useEffect(() => {
    let active = true;
    setState((current) => ({
      loading: true,
      error: "",
      data: keepPreviousData && current.hasLoaded ? current.data : fallback,
      hasLoaded: current.hasLoaded,
    }));
    apiGet(path)
      .then((data) => {
        if (active) setState({ loading: false, error: "", data, hasLoaded: true });
      })
      .catch((error) => {
        if (active) {
          setState((current) => ({
            loading: false,
            error: error.message,
            data: keepPreviousData && current.hasLoaded ? current.data : fallback,
            hasLoaded: current.hasLoaded,
          }));
        }
      });
    return () => {
      active = false;
    };
  }, [path]);

  return state;
}

function useNetWorthSeries(currency, timeline, reload) {
  const fallback = { net_worth_series: [] };
  const [state, setState] = useState({ loading: true, error: "", data: fallback, hasLoaded: false });

  useEffect(() => {
    let active = true;
    const query = `currency=${currency}&timeline=${timeline}&reload=${reload}`;
    setState((current) => ({
      loading: true,
      error: "",
      data: current.hasLoaded ? current.data : fallback,
      hasLoaded: current.hasLoaded,
    }));

    apiGet(`/api/net-worth-series?${query}`)
      .then((data) => {
        if (active) setState({ loading: false, error: "", data, hasLoaded: true });
      })
      .catch((error) => {
        if (active) {
          setState((current) => ({
            loading: false,
            error: error.message,
            data: current.hasLoaded ? current.data : fallback,
            hasLoaded: current.hasLoaded,
          }));
        }
      });

    return () => {
      active = false;
    };
  }, [currency, timeline, reload]);

  return state;
}

function useScrollSaver(loading) {
  const restoreScroll = useRef(null);

  useEffect(() => {
    if (loading || restoreScroll.current === null) {
      return;
    }

    const y = restoreScroll.current;
    restoreScroll.current = null;
    window.requestAnimationFrame(() => window.scrollTo({ top: y }));
  }, [loading]);

  return () => {
    restoreScroll.current = window.scrollY;
  };
}

function navigate(path) {
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

function App() {
  const [path, setPath] = useState(cleanPath(window.location.pathname));
  const [currency, setCurrency] = useState(localStorage.getItem("financeHubCurrency") || "NZD");
  const [pinned, setPinned] = useState(localStorage.getItem("financeHubSidebarPinned") === "true");
  const [open, setOpen] = useState(pinned);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {
    const onPop = () => setPath(cleanPath(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => {
    localStorage.setItem("financeHubCurrency", currency);
  }, [currency]);

  useEffect(() => {
    localStorage.setItem("financeHubSidebarPinned", String(pinned));
    if (pinned) setOpen(true);
  }, [pinned]);

  useEffect(() => {
    setMobileMenuOpen(false);
  }, [path]);

  useEffect(() => {
    if (!mobileMenuOpen) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === "Escape") setMobileMenuOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [mobileMenuOpen]);

  const pageTitle = titleFor(path);

  return (
    <div
      className={`app-shell ${pinned ? "sidebar-pinned" : ""} ${open ? "sidebar-open" : "sidebar-collapsed"}`}
    >
      <aside
        className="sidebar"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => {
          if (!pinned) setOpen(false);
        }}
        onClick={() => setOpen(true)}
      >
        <div className="sidebar-header">
          <button className="brand" onClick={() => navigate("/")}>
            <span className="brand-mark"><BrandIcon /></span>
            <span>Finance Hub</span>
          </button>
          {open && (
            <button className="pin-button" onClick={(event) => {
              event.stopPropagation();
              setPinned(!pinned);
            }} aria-label={pinned ? "Unpin sidebar" : "Pin sidebar"} title={pinned ? "Unpin sidebar" : "Pin sidebar"}>
              <span className={`pin-shape ${pinned ? "pinned" : ""}`} />
            </button>
          )}
        </div>
        <nav>
          {PAGES.map((page) => (
            <button
              key={page.path}
              className={path === page.path ? "active" : ""}
              aria-current={path === page.path ? "page" : undefined}
              onClick={() => navigate(page.path)}
            >
              <span className="nav-icon"><Icon path={page.path} /></span>
              <span className="nav-label">{page.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="profile-mark">F</span>
          <span>Finance Hub</span>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="topbar-title">
            <button
              className="mobile-nav-toggle"
              type="button"
              aria-controls="mobile-navigation"
              aria-expanded={mobileMenuOpen}
              aria-label={mobileMenuOpen ? "Close navigation" : "Open navigation"}
              onClick={() => setMobileMenuOpen((current) => !current)}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M4 7h16M4 12h16M4 17h16" />
              </svg>
            </button>
            <div>
              <h1>{pageTitle}</h1>
              <p>{PAGE_COPY[path]}</p>
            </div>
          </div>
          <div className="top-actions">
            {MONEY_PATHS.has(path) && <CurrencySwitch currency={currency} setCurrency={setCurrency} />}
            <StatusPill />
          </div>
        </header>
        <nav id="mobile-navigation" className={`mobile-nav ${mobileMenuOpen ? "open" : ""}`} aria-label="Mobile navigation">
          {PAGES.map((page) => (
            <button
              key={page.path}
              className={path === page.path ? "active" : ""}
              aria-current={path === page.path ? "page" : undefined}
              onClick={() => navigate(page.path)}
            >
              <span className="nav-icon"><Icon path={page.path} /></span>
              <span>{page.label}</span>
            </button>
          ))}
        </nav>
        <Page path={path} currency={currency} setCurrency={setCurrency} />
      </main>
    </div>
  );
}

function CurrencySwitch({ currency, setCurrency }) {
  return (
    <div className="segmented">
      {["NZD", "USD"].map((item) => (
        <button key={item} className={currency === item ? "active" : ""} onClick={() => setCurrency(item)}>
          {item}
        </button>
      ))}
    </div>
  );
}

function StatusPill() {
  const { loading, data } = useApi("/api/status", { database_found: false });
  return (
    <span className="status-pill" role="status" aria-live="polite">
      <span className={loading ? "dot checking" : data.database_found ? "dot good" : "dot bad"} />
      {loading ? "Checking database" : data.database_found ? "Database found" : "Database missing"}
    </span>
  );
}

function Page({ path, currency, setCurrency }) {
  if (path === "/accounts") return <Accounts currency={currency} />;
  if (path === "/transactions") return <Transactions currency={currency} />;
  if (path === "/cash-flow") return <CashFlow currency={currency} />;
  if (path === "/reports") return <Reports currency={currency} />;
  if (path === "/budgets") return <Budgets currency={currency} />;
  if (path === "/recurring") return <Recurring currency={currency} />;
  if (path === "/goals") return <Goals currency={currency} />;
  if (path === "/investments") return <Investments currency={currency} />;
  if (path === "/rules") return <Rules />;
  if (path === "/sync") return <SyncStatus />;
  if (path === "/settings") return <Settings currency={currency} setCurrency={setCurrency} />;
  return <Dashboard currency={currency} />;
}

function LoadingCard() {
  return <section className="card muted-card" role="status" aria-live="polite">Loading</section>;
}

function ErrorCard({ message }) {
  if (!message) return null;
  return <section className="card error-card" role="alert">{message}</section>;
}

function Card({ title, note, action, children, className = "" }) {
  return (
    <section className={`card ${className}`}>
      <div className="card-header">
        <div>
          <h2>{title}</h2>
          {note && <p>{note}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

function StatCard({ label, value, tone = "" }) {
  return (
    <section className="stat-card">
      <strong className={tone}>{value}</strong>
      <span>{label}</span>
    </section>
  );
}

function Dashboard({ currency }) {
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState("");
  const [refreshError, setRefreshError] = useState("");
  const [reload, setReload] = useState(0);
  const [netWorthTimeline, setNetWorthTimeline] = useState("1m");
  const [spendingComparison, setSpendingComparison] = useState("month_last");
  const { loading, error, data, hasLoaded } = useApi(
    `/api/dashboard?currency=${currency}&reload=${reload}`,
    {},
    { keepPreviousData: true }
  );
  const { error: netWorthError, data: netWorthChart } = useNetWorthSeries(currency, netWorthTimeline, reload);
  const saveScroll = useScrollSaver(loading);

  async function refreshAll() {
    setRefreshing(true);
    setRefreshMessage("");
    setRefreshError("");
    try {
      const result = await apiPost("/api/refresh-all", { currency, confirmation: "REFRESH" });
      setRefreshMessage(result.message || "Refresh finished");
      saveScroll();
      setReload((current) => current + 1);
    } catch (refreshError) {
      setRefreshError(refreshError.message);
    } finally {
      setRefreshing(false);
    }
  }

  if (loading && !hasLoaded) return <LoadingCard />;

  const month = data.month || {};
  const netWorth = data.net_worth || {};
  const openPeriod = (row) => navigate(transactionsPath({ start: row.date, end: row.end_date || row.date }));

  return (
    <>
      <ErrorCard message={error || refreshError || netWorthError} />
      {refreshMessage && <section className="card success-card">{refreshMessage}</section>}
      <div className="hero-card">
        <div>
          <h2>Your financial overview</h2>
          <p>This is the snapshot from the local database.</p>
        </div>
        <div className="hero-actions">
          <button className="primary" onClick={refreshAll} disabled={refreshing}>
            {refreshing ? "Refreshing" : "Refresh all"}
          </button>
          <button onClick={() => navigate("/transactions?review=not_reviewed")}>Review transactions</button>
        </div>
      </div>
      <div className="stat-grid">
        <StatCard label="Net worth" value={moneyFromMinor(netWorth.total_minor, currency)} />
        <StatCard label="Income this month" value={moneyFromMinor(month.income_total_minor, currency)} tone="positive" />
        <StatCard label="Spending this month" value={moneyFromMinor(month.spending_total_minor, currency)} tone="negative" />
        <StatCard label="Needs review" value={String(data.needs_review_count || 0)} tone="warn" />
      </div>
      <div className="dashboard-grid">
        <Card title="Net worth" note={data.rate_note}>
          <div className="chart-toolbar">
            <strong className="big-number">{moneyFromMinor(netWorth.total_minor, currency)}</strong>
            <TimelineTabs value={netWorthTimeline} onChange={setNetWorthTimeline} />
          </div>
          <LineChart series={netWorthChart.net_worth_series || []} currency={currency} onRowClick={openPeriod} />
        </Card>
        <SpendingComparisonPanel
          comparisons={data.spending_comparisons || []}
          selectedKey={spendingComparison}
          setSelectedKey={setSpendingComparison}
        />
        <Card title="Budget" note="Current month">
          <BudgetMini summary={data.budget_summary || {}} />
        </Card>
        <Card title="Recent transactions" note={`${data.needs_review_count || 0} need review`} action={<button onClick={() => navigate("/transactions")}>View all</button>}>
          <TransactionList transactions={data.recent_transactions || []} currency={currency} />
        </Card>
        <Card title="Recurring" note="Next 30 days">
          <RecurringList items={(data.recurring || {}).upcoming || []} currency={currency} limit={5} />
        </Card>
      </div>
    </>
  );
}

function BudgetMini({ summary }) {
  const income = summary.income || {};
  const expenses = summary.expenses || {};
  const leftToBudget = Number(summary.left_to_budget_minor || 0);
  const overBudget = leftToBudget < 0;
  return (
    <div className="budget-mini">
      <div className={`left-to-budget ${overBudget ? "over" : ""}`}>
        <strong>{moneyNoCurrency(leftToBudget)}</strong>
        <span>{overBudget ? "Over budget" : "Left to budget"}</span>
      </div>
      <div className="summary-line">
        <span>Income</span>
        <strong>{moneyNoCurrency(income.planned_minor)} planned</strong>
      </div>
      <ProgressLine amount={income.actual_minor} total={income.planned_minor} tone="income" />
      <div className="split-line">
        <span><strong>{moneyNoCurrency(income.actual_minor)}</strong> received</span>
        <span><strong>{moneyNoCurrency(income.remaining_minor)}</strong> remaining</span>
      </div>
      <div className="summary-line">
        <span>Expenses</span>
        <strong>{moneyNoCurrency(expenses.planned_minor)} planned</strong>
      </div>
      <ProgressLine amount={expenses.actual_minor} total={expenses.planned_minor} tone="expense" />
      <div className="split-line">
        <span><strong>{moneyNoCurrency(expenses.actual_minor)}</strong> spent</span>
        <span><strong>{moneyNoCurrency(expenses.remaining_minor)}</strong> remaining</span>
      </div>
    </div>
  );
}

function SpendingComparisonPanel({ comparisons, selectedKey, setSelectedKey }) {
  if (!comparisons.length) {
    return <Card title="Spending" note="No comparison data yet." />;
  }

  const selected = comparisons.find((comparison) => comparison.key === selectedKey) || comparisons[0];
  return (
    <section className="card comparison-chart-card">
      <div className="comparison-chart-head">
        <h2>
          Spending <span>{moneyNoCurrency(selected.amount_minor)} {selected.period_label}</span>
        </h2>
        <label className="comparison-select">
          <select value={selected.key} onChange={(event) => setSelectedKey(event.target.value)} aria-label="Spending comparison">
            {comparisons.map((comparison) => (
              <option key={comparison.key} value={comparison.key}>{comparison.label}</option>
            ))}
          </select>
        </label>
      </div>
      <SpendingComparisonChart comparison={selected} />
    </section>
  );
}

function SpendingComparisonChart({ comparison }) {
  const points = comparison.points || [];
  if (!points.length) return <p className="muted">No comparison data yet.</p>;

  const pointValue = (point, key) => {
    if (point[key] === null || point[key] === undefined) return null;
    const value = Number(point[key]);
    return Number.isFinite(value) ? value : null;
  };
  const chartValues = points
    .flatMap((point) => [pointValue(point, "current_minor"), pointValue(point, "reference_minor")])
    .filter((value) => value !== null);
  const minValue = Math.min(0, ...chartValues);
  const maxValue = Math.max(0, ...chartValues, 1);
  const tickValues = niceTickValues(minValue < 0 ? minValue * 1.08 : 0, maxValue * 1.08, 5);
  const lower = tickValues[0];
  const upper = tickValues[tickValues.length - 1];
  const range = Math.max(upper - lower, 1);
  const left = 62;
  const right = 706;
  const top = 28;
  const bottom = 196;
  const width = right - left;
  const labelEvery = Math.max(1, Math.ceil(points.length / 8));
  const xForIndex = (index) => left + index * (width / Math.max(points.length - 1, 1));
  const yForValue = (value) => bottom - ((Number(value || 0) - lower) / range) * (bottom - top);
  const currentPointRows = points
    .map((point, index) => ({ value: pointValue(point, "current_minor"), x: xForIndex(index) }))
    .filter((point) => point.value !== null)
    .map((point) => ({ ...point, y: yForValue(point.value) }));
  const referencePointRows = points
    .map((point, index) => ({ value: pointValue(point, "reference_minor"), x: xForIndex(index) }))
    .filter((point) => point.value !== null)
    .map((point) => ({ ...point, y: yForValue(point.value) }));
  const currentPoints = currentPointRows.map((point) => `${point.x},${point.y}`).join(" ");
  const referencePoints = referencePointRows.map((point) => `${point.x},${point.y}`).join(" ");
  const firstCurrent = currentPointRows[0];
  const lastCurrent = currentPointRows[currentPointRows.length - 1];
  const zeroY = yForValue(0);
  const labelIndexes = Array.from(points.keys())
    .filter((index) => index % labelEvery === 0 || index === points.length - 1);
  if (labelIndexes.length > 1) {
    const lastIndex = labelIndexes[labelIndexes.length - 1];
    const previousIndex = labelIndexes[labelIndexes.length - 2];
    if (xForIndex(lastIndex) - xForIndex(previousIndex) < 70) {
      labelIndexes.splice(labelIndexes.length - 2, 1);
    }
  }

  return (
    <div className="comparison-chart-wrap">
      <svg viewBox="0 0 740 260" role="img" aria-label={comparison.label}>
        {tickValues.map((value) => {
          const y = yForValue(value);
          return (
            <g key={value}>
              <line x1={left} y1={y} x2={right} y2={y} className={value === 0 ? "axis" : "grid-line"} />
              <text x={left - 12} y={y + 4} className="axis-label">{chartAxisMoney(value, tickValues)}</text>
            </g>
          );
        })}
        {labelIndexes.map((index) => (
          <text key={`${points[index].label}-${index}`} x={xForIndex(index)} y="231" className="comparison-x-label">
            {points[index].label}
          </text>
        ))}
        {currentPointRows.length > 0 && (
          <polygon points={`${currentPoints} ${lastCurrent.x},${zeroY} ${firstCurrent.x},${zeroY}`} className="comparison-area" />
        )}
        {referencePointRows.length > 0 && <polyline points={referencePoints} className="comparison-reference-line" />}
        {currentPointRows.length > 0 && <polyline points={currentPoints} className="comparison-current-line" />}
        {lastCurrent && <circle cx={lastCurrent.x} cy={lastCurrent.y} r="3" className="comparison-current-dot" />}
      </svg>
      <div className="comparison-legend">
        <span className="comparison-reference-key" /> {comparison.reference_label}
        <span className="comparison-current-key" /> {comparison.current_label}
      </div>
    </div>
  );
}

function Accounts({ currency }) {
  const [timeline, setTimeline] = useState("1m");
  const [reload, setReload] = useState(0);
  const { loading, error, data, hasLoaded } = useApi(
    `/api/accounts?currency=${currency}&reload=${reload}`,
    { groups: {} },
    { keepPreviousData: true }
  );
  const { error: netWorthError, data: netWorthChart } = useNetWorthSeries(currency, timeline, reload);
  const saveScroll = useScrollSaver(loading);

  async function renameAccount(id, name) {
    await apiPost("/api/accounts/rename", { id, name });
    saveScroll();
    setReload((value) => value + 1);
  }

  if (loading && !hasLoaded) return <LoadingCard />;

  const groups = data.groups || {};
  const openPeriod = (row) => navigate(transactionsPath({ start: row.date, end: row.end_date || row.date }));
  return (
    <>
      <ErrorCard message={error || netWorthError} />
      <div className="accounts-layout">
        <div>
          <Card title="Net worth" note={data.rate_note}>
            <div className="chart-toolbar">
              <strong className="big-number">{moneyFromMinor(data.net_worth?.total_minor, currency)}</strong>
              <TimelineTabs value={timeline} onChange={setTimeline} />
            </div>
            <LineChart series={netWorthChart.net_worth_series || []} currency={currency} onRowClick={openPeriod} />
          </Card>
          {["Cash", "Investments", "Credit cards", "Loans"].map((group) => (
            <AccountGroup key={group} title={group} accounts={groups[group] || []} currency={currency} onRename={renameAccount} />
          ))}
        </div>
        <Card title="Summary">
          <SummaryBars summary={data.net_worth || {}} currency={currency} />
        </Card>
      </div>
    </>
  );
}

function AccountGroup({ title, accounts, currency, onRename }) {
  return (
    <Card title={title} note={`${accounts.length} accounts`} className="tight-card">
      <div className="account-list">
        {accounts.length === 0 && <p className="muted">No accounts in this group.</p>}
        {accounts.map((account) => (
          <div className="account-row" key={account.id}>
            <span className="account-mark">{title[0]}</span>
            <div className="account-name-cell">
              <EditableAccountName account={account} onRename={onRename} />
              <p>{account.institution || account.provider} | {account.account_type || "-"}</p>
            </div>
            <strong>{moneyFromMinor(account.display_balance_minor, currency)}</strong>
            <span className="soft-pill">{account.is_active ? "Active" : "Inactive"}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

function EditableAccountName({ account, onRename }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(account.name);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setName(account.name);
  }, [account.name]);

  async function saveName(event) {
    event.preventDefault();
    const cleanName = name.trim();
    if (!cleanName || cleanName === account.name) {
      setName(account.name);
      setEditing(false);
      return;
    }

    try {
      setSaving(true);
      setError("");
      await onRename(account.id, cleanName);
      setEditing(false);
    } catch (renameError) {
      setError(renameError.message);
    } finally {
      setSaving(false);
    }
  }

  if (editing) {
    return (
      <form className="account-name-form" onSubmit={saveName}>
        <input value={name} onChange={(event) => setName(event.target.value)} autoFocus />
        <button className="primary small" disabled={saving}>{saving ? "Saving" : "Save"}</button>
        <button type="button" className="small" onClick={() => {
          setName(account.name);
          setError("");
          setEditing(false);
        }}>
          Cancel
        </button>
        {error && <small className="negative">{error}</small>}
      </form>
    );
  }

  return (
    <div className="account-title-line">
      <button type="button" className="account-name-button" onClick={() => setEditing(true)} aria-label={`Edit ${account.name}`}>
        <strong>{account.name}</strong>
      </button>
    </div>
  );
}

function SummaryBars({ summary, currency }) {
  const assets = summary.asset_total_minor || 0;
  const liabilities = summary.liability_total_minor || 0;
  const total = Math.max(assets + liabilities, 1);
  return (
    <div className="summary-panel">
      <div className="summary-line"><span>Assets</span><strong>{moneyFromMinor(assets, currency)}</strong></div>
      <ProgressLine amount={assets} total={total} tone="income" />
      <div className="summary-line"><span>Liabilities</span><strong>{moneyFromMinor(liabilities, currency)}</strong></div>
      <ProgressLine amount={liabilities} total={total} tone="expense" />
      <div className="summary-line"><span>Net worth</span><strong>{moneyFromMinor(summary.total_minor, currency)}</strong></div>
    </div>
  );
}

function Transactions({ currency }) {
  const [filters, setFilters] = useState(() => Object.fromEntries(new URLSearchParams(window.location.search)));
  const [limit, setLimit] = useState(TRANSACTION_PAGE_SIZE);
  const [reload, setReload] = useState(0);
  const [localTransactions, setLocalTransactions] = useState([]);
  const [actionError, setActionError] = useState("");
  const restoreScroll = useRef(null);
  const query = new URLSearchParams({ currency, ...filters, limit, reload }).toString();
  const { loading, error, data } = useApi(`/api/transactions?${query}`, {
    transactions: [],
    categories: [],
    accounts: [],
  }, { keepPreviousData: true });

  useEffect(() => {
    setLocalTransactions(data.transactions || []);
  }, [data.transactions]);

  useEffect(() => {
    if (!loading && restoreScroll.current !== null) {
      const y = restoreScroll.current;
      restoreScroll.current = null;
      window.requestAnimationFrame(() => window.scrollTo({ top: y }));
    }
  }, [loading]);

  function reloadKeepingSpot() {
    restoreScroll.current = window.scrollY;
    setReload((value) => value + 1);
  }

  function updateFilter(name, value) {
    setLimit(TRANSACTION_PAGE_SIZE);
    setFilters((current) => ({ ...current, [name]: value }));
  }

  async function saveReview(id, status) {
    try {
      setActionError("");
      await apiPost("/api/transactions/review", { id, review_status: status });
      setLocalTransactions((rows) => rows
        .map((row) => row.id === id ? { ...row, review_status: status } : row)
        .filter((row) => !filters.review || row.review_status === filters.review));
    } catch (error) {
      setActionError(error.message);
    }
  }

  async function saveCategory(id, category) {
    try {
      setActionError("");
      await apiPost("/api/transactions/category", { id, category });
      setLocalTransactions((rows) => rows
        .map((row) => row.id === id ? { ...row, category: category || "Uncategorized" } : row)
        .filter((row) => !filters.category || row.category === filters.category));
    } catch (error) {
      setActionError(error.message);
    }
  }

  async function saveSplit(id, values) {
    try {
      setActionError("");
      await apiPost("/api/transactions/split", { id, currency, ...values });
      reloadKeepingSpot();
    } catch (error) {
      setActionError(error.message);
    }
  }

  async function clearSplit(id) {
    try {
      setActionError("");
      await apiPost("/api/transactions/split-clear", { id });
      reloadKeepingSpot();
    } catch (error) {
      setActionError(error.message);
    }
  }

  return (
    <>
      <ErrorCard message={error || actionError} />
      <Card title="Filters" note="Search, dates, account, category, and status" className="filter-card">
        <div className="filter-grid">
          <Field label="Search"><input value={filters.search || ""} onChange={(e) => updateFilter("search", e.target.value)} placeholder="Merchant or description" /></Field>
          <Field label="Date from"><input type="date" value={filters.start || ""} onChange={(e) => updateFilter("start", e.target.value)} /></Field>
          <Field label="Date to"><input type="date" value={filters.end || ""} onChange={(e) => updateFilter("end", e.target.value)} /></Field>
          <Field label="Account"><Select value={filters.account || ""} onChange={(value) => updateFilter("account", value)} options={["", ...(data.accounts || []).map((item) => item.name)]} emptyLabel="All accounts" /></Field>
          <Field label="Category"><GroupedCategorySelect value={filters.category || ""} onChange={(value) => updateFilter("category", value)} categories={data.categories || []} /></Field>
          <Field label="Status"><Select value={filters.status || ""} onChange={(value) => updateFilter("status", value)} options={["", "pending", "posted"]} emptyLabel="All statuses" /></Field>
        </div>
      </Card>
      {loading && !localTransactions.length ? <LoadingCard /> : (
        <Card
          title="Transactions"
          note={`Showing ${localTransactions.length} rows from ${data.total_count} transactions`}
          action={<div className="card-header-actions"><ReviewTabs value={filters.review || ""} onChange={(value) => updateFilter("review", value)} /><span className={`inline-update-status ${loading ? "visible" : ""}`} role="status" aria-live="polite">{loading ? "Updating…" : ""}</span></div>}
        >
          <TransactionTable
            transactions={localTransactions}
            categories={data.categories}
            currency={currency}
            onReview={saveReview}
            onCategory={saveCategory}
            onSplit={saveSplit}
            onClearSplit={clearSplit}
            busy={loading}
          />
          {localTransactions.length < data.total_count && limit < MAX_TRANSACTION_LIMIT && (
            <button className="load-more" onClick={() => setLimit((value) => Math.min(value + TRANSACTION_PAGE_SIZE, MAX_TRANSACTION_LIMIT))}>
              Load more transactions
            </button>
          )}
        </Card>
      )}
    </>
  );
}

function ReviewTabs({ value, onChange }) {
  return (
    <div className="segmented">
      <button className={value === "" ? "active" : ""} onClick={() => onChange("")}>All</button>
      <button className={value === "not_reviewed" ? "active" : ""} onClick={() => onChange("not_reviewed")}>Needs review</button>
      <button className={value === "reviewed" ? "active" : ""} onClick={() => onChange("reviewed")}>Reviewed</button>
    </div>
  );
}

function TransactionTable({ transactions, categories, currency, onReview, onCategory, onSplit, onClearSplit, busy }) {
  const [splitOpenId, setSplitOpenId] = useState(null);
  let lastDate = "";
  return (
    <div className="transaction-table" aria-busy={busy}>
      <div className="table-head">
        <span>Description</span><span>Category</span><span>Account</span><span>Status</span><span>Review</span><span>Amount</span>
      </div>
      {transactions.length === 0 && <p className="muted padded">No transactions found.</p>}
      {transactions.map((transaction) => {
        const showDate = transaction.posted_date !== lastDate;
        lastDate = transaction.posted_date;
        const actionId = transaction.source_id || transaction.id;
        const splitIsOpen = splitOpenId === transaction.id;
        const isSplit = Boolean(transaction.is_split_line);
        return (
          <React.Fragment key={transaction.id}>
            {showDate && <div className="date-row">{transaction.posted_date}</div>}
            <div className="transaction-row" id={`transaction-${transaction.id}`}>
              <div className="transaction-cell" data-label="Description">
                <div className="transaction-name">
                  <span className="money-dot">$</span>
                  <div><strong>{transaction.description}</strong><p>{transaction.notes || ""}</p></div>
                </div>
              </div>
              <div className="transaction-cell" data-label="Category">
                {isSplit ? (
                  <span className="soft-pill split-category-pill">{transaction.category}</span>
                ) : (
                  <GroupedCategorySelect value={transaction.category || "Uncategorized"} onChange={(value) => onCategory(transaction.id, value)} categories={categories} compact />
                )}
              </div>
              <div className="transaction-cell" data-label="Account"><span>{transaction.account}</span></div>
              <div className="transaction-cell" data-label="Status"><span className="soft-pill">{transaction.transaction_status}</span></div>
              <div className="transaction-cell" data-label="Review">
                <div className="review-actions">
                  <button className="primary small" onClick={() => onReview(actionId, transaction.review_status === "reviewed" ? "not_reviewed" : "reviewed")}>
                    {transaction.review_status === "reviewed" ? "Reviewed" : "Mark reviewed"}
                  </button>
                  <button className="small" onClick={() => setSplitOpenId(splitIsOpen ? null : transaction.id)}>
                    {isSplit ? "Edit split" : "Split"}
                  </button>
                </div>
              </div>
              <div className="transaction-cell transaction-amount" data-label="Amount">
                <strong className={`amount ${transaction.amount_minor < 0 ? "negative" : "positive"}`}>{moneyFromMinor(transaction.amount_minor, currency)}</strong>
              </div>
            </div>
            {splitIsOpen && (
              <SplitPanel
                transaction={transaction}
                categories={categories}
                onSave={onSplit}
                onClear={onClearSplit}
              />
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}

function SplitPanel({ transaction, categories, onSave, onClear }) {
  const initialSplits = transaction.splits?.length
    ? transaction.splits.map((split) => ({
      category: split.category || "",
      amount: moneyInputFromMinor(split.amount_minor),
    }))
    : [
      { category: "", amount: "" },
      { category: "", amount: "" },
    ];
  const [splits, setSplits] = useState(initialSplits);
  const total = Math.abs(transaction.parent_amount_minor ?? transaction.amount_minor ?? 0);
  const enteredTotal = splits.reduce((sum, split) => sum + Math.abs(Math.round(Number(split.amount || 0) * 100)), 0);
  const remaining = total - enteredTotal;
  const matchesTotal = Math.round(enteredTotal) === total;
  const actionId = transaction.source_id || transaction.id;

  function updateSplit(index, changes) {
    setSplits((rows) => rows.map((row, rowIndex) => rowIndex === index ? { ...row, ...changes } : row));
  }

  function addSplit() {
    setSplits((rows) => [...rows, { category: "", amount: "" }]);
  }

  function removeSplit(index) {
    setSplits((rows) => rows.filter((_, rowIndex) => rowIndex !== index));
  }

  async function save(event) {
    event.preventDefault();
    if (!matchesTotal) return;
    await onSave(actionId, {
      splits: splits.map((split) => ({
        category: split.category,
        amount: split.amount,
      })),
    });
  }

  return (
    <form className="split-panel" onSubmit={save}>
      <div className="split-panel-head">
        <strong>Split transaction</strong>
        <p>Total must equal {moneyAmountOnly(total)}.</p>
      </div>
      <div className="split-lines">
        {splits.map((split, index) => (
          <div className="split-edit-row" key={index}>
            <GroupedCategorySelect value={split.category} onChange={(category) => updateSplit(index, { category })} categories={categories} emptyLabel={`Category ${index + 1}`} />
            <input value={split.amount} onChange={(event) => updateSplit(index, { amount: event.target.value })} placeholder="Amount" />
            {splits.length > 2 && (
              <button type="button" className="small" onClick={() => removeSplit(index)}>Remove</button>
            )}
          </div>
        ))}
      </div>
      <div className={matchesTotal ? "split-total good" : "split-total bad"}>
        {remaining < 0 ? "-" : ""}{moneyAmountOnly(remaining)} remaining
      </div>
      <div className="split-actions">
        <button type="button" className="small" onClick={addSplit}>Add category</button>
        <button className="primary small" disabled={!matchesTotal}>Save split</button>
        {transaction.splits?.length > 0 && (
          <button type="button" className="small" onClick={() => onClear(actionId)}>Clear split</button>
        )}
      </div>
    </form>
  );
}

function TransactionList({ transactions, currency }) {
  if (!transactions.length) return <p className="muted">No transactions found.</p>;
  return (
    <div className="mini-list">
      {transactions.map((transaction) => (
        <button key={transaction.id} className="mini-row" onClick={() => navigate(`/transactions#transaction-${transaction.id}`)}>
          <span className="money-dot">$</span>
          <span><strong>{transaction.description}</strong><small>{transaction.category} | {transaction.account}</small></span>
          <strong className={transaction.amount_minor < 0 ? "negative" : "positive"}>{moneyFromMinor(transaction.amount_minor, currency)}</strong>
        </button>
      ))}
    </div>
  );
}

function Budgets({ currency }) {
  const [weekStart, setWeekStart] = useState("");
  const [sort, setSort] = useState("planned");
  const [reload, setReload] = useState(0);
  const [expandedGroups, setExpandedGroups] = useState({});
  const [saving, setSaving] = useState(false);
  const [showSaving, setShowSaving] = useState(false);
  const saveQueue = useRef(Promise.resolve());
  const saveSequence = useRef(0);
  const saveSucceeded = useRef(false);
  const query = new URLSearchParams({ currency, view: "month", week_start: weekStart, sort, reload }).toString();
  const { loading, error, data } = useApi(
    `/api/budgets?${query}`,
    { groups: [], summary: {} },
    { keepPreviousData: true }
  );

  useEffect(() => {
    if (!weekStart && data.week_start) setWeekStart(data.week_start);
  }, [data.week_start, weekStart]);

  useEffect(() => {
    if (!saving && !loading) {
      setShowSaving(false);
      return undefined;
    }
    const timeout = window.setTimeout(() => setShowSaving(true), 180);
    return () => window.clearTimeout(timeout);
  }, [saving, loading]);

  async function saveBudget(row, changes) {
    const payload = {
      category: row.category,
      week_start: data.week_start,
      budget_currency: changes.budget_currency ?? row.currency ?? currency,
      amount: changes.amount ?? "",
      income_percent: changes.income_percent ?? "",
      rollover_enabled: String(changes.rollover_enabled ?? row.rollover_enabled ?? false),
      budget_source: changes.budget_source ?? "",
      currency,
      view: "month",
      budget_sort: sort,
    };
    const sequence = saveSequence.current + 1;
    saveSequence.current = sequence;
    setSaving(true);

    const request = saveQueue.current.then(() => apiPost("/api/budgets/save", payload));
    saveQueue.current = request.catch(() => undefined);

    try {
      await request;
      saveSucceeded.current = true;
    } finally {
      if (sequence === saveSequence.current) {
        if (saveSucceeded.current) setReload((value) => value + 1);
        saveSucceeded.current = false;
        setSaving(false);
      }
    }
  }

  async function addCategory(event) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const groupName = form.get("group_name");
    await apiPost("/api/budgets/category", {
      ...Object.fromEntries(form.entries()),
      currency,
      view: "month",
      week_start: data.week_start,
      budget_sort: sort,
    });
    formElement.reset();
    if (groupName) {
      setExpandedGroups((current) => ({ ...current, [groupName]: true }));
    }
    setReload((value) => value + 1);
  }

  if (loading && !(data.groups || []).length) return <LoadingCard />;

  return (
    <>
      <ErrorCard message={error} />
      <div className="budget-top">
        <div>
          <h2>{data.title}</h2>
          <p>Monthly view uses the selected month</p>
        </div>
        <div className="top-actions">
          <button onClick={() => setWeekStart(data.previous_start)}>Previous month</button>
          <button onClick={() => setWeekStart(data.current_start)}>Current month</button>
          <button onClick={() => setWeekStart(data.next_start)}>Next month</button>
        </div>
      </div>
      <div className="budget-layout">
        <Card
          title="Budget categories"
          note="Type a dollar amount or a percent and it saves that row"
          action={<span className={`budget-save-status ${showSaving ? "visible" : ""}`} role="status" aria-live="polite">{showSaving ? "Updating budget…" : ""}</span>}
        >
          <details className="add-category">
            <summary>Add a budget category</summary>
            <form className="inline-form" onSubmit={addCategory}>
              <input name="name" placeholder="Category name" required />
              <select name="group_name" defaultValue="Variable Expenses">
                {(data.category_groups || []).map((group) => <option key={group}>{group}</option>)}
              </select>
              <select name="category_type" defaultValue="expense">
                <option value="expense">Expense</option>
                <option value="income">Income</option>
              </select>
              <button className="primary">Add category</button>
            </form>
          </details>
          <div className="sort-row">
            <button className={sort === "planned" ? "active-link" : ""} onClick={() => setSort("planned")}>Sort by planned</button>
            <button className={sort === "actual" ? "active-link" : ""} onClick={() => setSort("actual")}>Sort by actual</button>
            <button className={sort === "left_over" ? "active-link" : ""} onClick={() => setSort("left_over")}>Sort by left over</button>
            <button className={sort === "over_budget" ? "active-link" : ""} onClick={() => setSort("over_budget")}>Sort by over budget</button>
          </div>
          <BudgetGroups groups={data.groups || []} currency={currency} onSave={saveBudget} weekStart={data.week_start} expandedGroups={expandedGroups} busy={saving || loading} />
        </Card>
        <BudgetSidePanel summary={data.summary || {}} />
      </div>
    </>
  );
}

function BudgetGroups({ groups, currency, onSave, weekStart, expandedGroups, busy }) {
  return (
    <div className="budget-table" aria-busy={busy}>
      {groups.map((group) => (
        <BudgetGroup key={group.name} group={group} currency={currency} onSave={onSave} weekStart={weekStart} forceShowUnbudgeted={Boolean(expandedGroups[group.name])} />
      ))}
    </div>
  );
}

function BudgetGroup({ group, currency, onSave, weekStart, forceShowUnbudgeted }) {
  const [showUnbudgeted, setShowUnbudgeted] = useState(false);
  const visibleRows = group.rows.filter((row) => row.is_budgeted || showUnbudgeted);
  const unbudgetedCount = group.rows.filter((row) => !row.is_budgeted).length;

  useEffect(() => {
    if (forceShowUnbudgeted) setShowUnbudgeted(true);
  }, [forceShowUnbudgeted]);

  return (
    <section className="budget-group">
      <div className="budget-group-title">
        <h3>{group.name}</h3>
        <div className="budget-totals">
          <span><small>Planned</small><strong>{moneyNoCurrency(group.planned_minor)}</strong></span>
          <span><small>Planned %</small><strong>{group.planned_percent_of_income !== "" && group.planned_percent_of_income !== undefined ? `${group.planned_percent_of_income}%` : "—"}</strong></span>
          <span><small>Actual</small><strong>{moneyNoCurrency(group.actual_minor)}</strong></span>
          <span><small>Remaining</small><strong className={group.remaining_minor < 0 ? "negative" : "positive"}>{moneyNoCurrency(group.remaining_minor)}</strong></span>
        </div>
      </div>
      <div className="budget-head">
        <span>Category</span><span>Planned $</span><span>Planned %</span><span>Actual</span><span>Remaining</span><span>Rollover</span>
      </div>
      {visibleRows.map((row) => <BudgetRow key={`${group.name}-${row.category}`} row={row} currency={currency} onSave={onSave} weekStart={weekStart} />)}
      {unbudgetedCount > 0 && (
        <button className="show-unbudgeted" type="button" onClick={() => setShowUnbudgeted(!showUnbudgeted)}>
          {showUnbudgeted ? "Hide" : "Show"} {unbudgetedCount} unbudgeted
        </button>
      )}
    </section>
  );
}

function BudgetRow({ row, currency, onSave, weekStart }) {
  const [amount, setAmount] = useState(row.amount_input || "");
  const [incomePercent, setIncomePercent] = useState(row.percent_of_income_input || "");
  const [editSource, setEditSource] = useState("amount");
  const [rollover, setRollover] = useState(Boolean(row.rollover_enabled));
  const [dirty, setDirty] = useState(false);
  const [saveStatus, setSaveStatus] = useState("");
  const saveAttempt = useRef(0);
  const savedTimer = useRef(null);

  useEffect(() => {
    setAmount(row.amount_input || "");
    setIncomePercent(row.percent_of_income_input || "");
    setEditSource("amount");
    setRollover(Boolean(row.rollover_enabled));
    setDirty(false);
  }, [row.amount_input, row.percent_of_income_input, row.rollover_enabled]);

  useEffect(() => () => window.clearTimeout(savedTimer.current), []);

  async function persist(changes) {
    const attempt = saveAttempt.current + 1;
    saveAttempt.current = attempt;
    window.clearTimeout(savedTimer.current);
    setSaveStatus("saving");
    try {
      await onSave(row, changes);
      if (attempt === saveAttempt.current) {
        setSaveStatus("saved");
        savedTimer.current = window.setTimeout(() => setSaveStatus(""), 1200);
      }
    } catch {
      if (attempt === saveAttempt.current) setSaveStatus("error");
    }
  }

  function saveAmount() {
    if (!dirty) return;
    setDirty(false);
    const budgetSource = editSource === "percent" && incomePercent.trim() ? "percent" : "amount";
    void persist({
      amount,
      income_percent: incomePercent,
      budget_currency: row.currency || currency,
      rollover_enabled: rollover,
      budget_source: budgetSource,
    });
  }

  return (
    <div className="budget-row">
      <span>
        <button className="budget-category-link" type="button" onClick={() => navigate(budgetTransactionPath(row, weekStart))}>{row.category}</button>
        <small className={`budget-row-note ${saveStatus === "error" ? "negative" : ""}`} aria-live="polite">
          {saveStatus === "saving" ? "Saving…" : saveStatus === "saved" ? "Saved" : saveStatus === "error" ? "Couldn’t save" : row.percent_of_income || ""}
        </small>
      </span>
      <input aria-label={`${row.category} planned amount`} inputMode="decimal" value={amount} onChange={(event) => {
        setAmount(event.target.value);
        setEditSource("amount");
        setDirty(true);
      }} onBlur={saveAmount} onKeyDown={(event) => event.key === "Enter" && event.currentTarget.blur()} />
      <div className="percent-input-wrap">
        <input aria-label={`${row.category} planned percent`} inputMode="decimal" value={incomePercent} onChange={(event) => {
          setIncomePercent(event.target.value);
          setEditSource("percent");
          setDirty(true);
        }} onBlur={saveAmount} placeholder="0.0" disabled={!row.income_base_minor} />
        <span aria-hidden="true">%</span>
      </div>
      <span>{moneyNoCurrency(row.actual_minor)}</span>
      <strong className={row.remaining_minor < 0 ? "negative" : "positive"}>{moneyNoCurrency(row.remaining_minor)}</strong>
      <select aria-label={`${row.category} rollover`} value={String(rollover)} disabled={row.is_income} onChange={(event) => {
        const enabled = event.target.value === "true";
        setRollover(enabled);
        const budgetSource = editSource === "percent" && incomePercent.trim() ? "percent" : "amount";
        void persist({ amount, income_percent: incomePercent, budget_currency: row.currency || currency, rollover_enabled: enabled, budget_source: budgetSource });
      }}>
        <option value="false">Off</option>
        <option value="true">On</option>
      </select>
    </div>
  );
}

function BudgetSidePanel({ summary }) {
  return (
    <aside className="budget-side">
      <BudgetMini summary={summary} />
    </aside>
  );
}

function CashFlow({ currency }) {
  const startParams = new URLSearchParams(window.location.search);
  const [timeline, setTimeline] = useState(startParams.get("timeline") || "1m");
  const [chartType, setChartType] = useState(startParams.get("chart") || "bars");
  const { loading, error, data, hasLoaded } = useApi(
    `/api/cash-flow?currency=${currency}&timeline=${timeline}`,
    { summary: {}, monthly_series: [] },
    { keepPreviousData: true }
  );
  if (loading && !hasLoaded) return <LoadingCard />;
  const range = chartDateRange(data.summary);
  const openCategory = (row) => navigate(transactionsPath({ category: row.category_name, ...range }));
  const openPeriod = (row) => navigate(transactionsPath({ start: row.date, end: row.end_date || row.date }));

  return (
    <>
      <ErrorCard message={error} />
      <div className="chart-toolbar" aria-busy={loading}>
        <TimelineTabs value={timeline} onChange={setTimeline} />
        <ChartTabs value={chartType} onChange={setChartType} includeCombo />
        <span className={`chart-update-status ${loading ? "visible" : ""}`} role="status" aria-live="polite">{loading ? "Updating…" : ""}</span>
      </div>
      <div className="stat-grid">
        <StatCard label="Income" value={moneyFromMinor(data.summary?.income_total_minor, currency)} tone="positive" />
        <StatCard label="Expenses" value={moneyFromMinor(data.summary?.spending_total_minor, currency)} tone="negative" />
        <StatCard label="Savings" value={moneyFromMinor(data.summary?.net_minor, currency)} tone={valueTone(data.summary?.net_minor)} />
        <StatCard label="Savings rate" value={`${data.savings_rate || 0}%`} tone={valueTone(data.savings_rate)} />
      </div>
      <Card title={chartType === "combo" ? "Cash flow trend" : "Expense breakdown"}>
        {chartType === "combo" ? <ComboChart series={data.monthly_series || []} currency={currency} onRowClick={openPeriod} /> : <CategoryVisual rows={data.summary?.spending_by_category || []} total={data.summary?.spending_total_minor || 0} currency={currency} type={chartType} tone="expense" onRowClick={openCategory} />}
      </Card>
      <CategorySections summary={data.summary || {}} currency={currency} onRowClick={openCategory} />
    </>
  );
}

function Reports({ currency }) {
  const startParams = new URLSearchParams(window.location.search);
  const [timeline, setTimeline] = useState(startParams.get("timeline") || "1m");
  const [view, setView] = useState(["cash-flow", "spending", "income"].includes(startParams.get("view")) ? startParams.get("view") : "spending");
  const [chartType, setChartType] = useState(["donut", "bars"].includes(startParams.get("chart")) ? startParams.get("chart") : "donut");
  const { loading, error, data, hasLoaded } = useApi(
    `/api/reports?currency=${currency}&timeline=${timeline}`,
    { summary: {}, monthly_series: [] },
    { keepPreviousData: true }
  );
  if (loading && !hasLoaded) return <LoadingCard />;

  const rows = view === "income" ? data.summary?.income_by_category || [] : data.summary?.spending_by_category || [];
  const total = view === "income" ? data.summary?.income_total_minor || 0 : data.summary?.spending_total_minor || 0;
  const range = chartDateRange(data.summary);
  const openCategory = (row) => navigate(transactionsPath({ category: row.category_name, ...range }));
  const openPeriod = (row) => navigate(transactionsPath({ start: row.date, end: row.end_date || row.date }));

  return (
    <>
      <ErrorCard message={error} />
      <div className="chart-toolbar" aria-busy={loading}>
        <div className="tab-row">
          {["cash-flow", "spending", "income"].map((item) => <button key={item} className={view === item ? "active-link" : ""} onClick={() => setView(item)}>{item.replace("-", " ")}</button>)}
        </div>
        <TimelineTabs value={timeline} onChange={setTimeline} />
        {view !== "cash-flow" && <ChartTabs value={chartType} onChange={setChartType} />}
        <span className={`chart-update-status ${loading ? "visible" : ""}`} role="status" aria-live="polite">{loading ? "Updating…" : ""}</span>
      </div>
      <div className="stat-grid">
        <StatCard label="Total income" value={moneyFromMinor(data.summary?.income_total_minor, currency)} tone="positive" />
        <StatCard label="Total expenses" value={moneyFromMinor(data.summary?.spending_total_minor, currency)} tone="negative" />
        <StatCard label="Net income" value={moneyFromMinor(data.summary?.net_minor, currency)} tone={valueTone(data.summary?.net_minor)} />
        <StatCard label="Savings rate" value={`${data.savings_rate || 0}%`} tone={valueTone(data.savings_rate)} />
      </div>
      <Card title={view === "cash-flow" ? "Cash flow trend" : `${view} breakdown`}>
        {view === "cash-flow" ? <ComboChart series={data.monthly_series || []} currency={currency} onRowClick={openPeriod} /> : <CategoryVisual rows={rows} total={total} currency={currency} type={chartType} tone={view === "income" ? "income" : "expense"} onRowClick={openCategory} />}
      </Card>
    </>
  );
}

function Recurring({ currency }) {
  const { loading, error, data, hasLoaded } = useApi(
    `/api/recurring?currency=${currency}`,
    {},
    { keepPreviousData: true }
  );
  if (loading && !hasLoaded) return <LoadingCard />;
  return (
    <>
      <ErrorCard message={error} />
      <div className="stat-grid">
        <StatCard label="Expected income" value={moneyFromMinor(data.income_minor, currency)} tone="positive" />
        <StatCard label="Expected expenses" value={moneyFromMinor(data.expense_minor, currency)} tone="negative" />
        <StatCard label="Net" value={moneyFromMinor(data.net_minor, currency)} tone={valueTone(data.net_minor)} />
        <StatCard label="Patterns" value={String(data.pattern_count || 0)} />
      </div>
      <Card title="Upcoming" note="Next 30 days">
        <RecurringList items={data.upcoming || []} currency={currency} />
      </Card>
    </>
  );
}

function RecurringList({ items, currency, limit }) {
  const shown = limit ? items.slice(0, limit) : items;
  if (!shown.length) return <p className="muted">No upcoming recurring items found.</p>;
  return <div className="mini-list">{shown.map((item, index) => (
    <div className="mini-row" key={`${item.due_date}-${item.description}-${index}`}>
      <span className="money-dot">R</span>
      <span><strong>{item.description}</strong><small>{item.due_date} | {item.frequency}</small></span>
      <strong className={item.amount_minor < 0 ? "negative" : "positive"}>{moneyFromMinor(item.amount_minor, currency)}</strong>
    </div>
  ))}</div>;
}

function Goals({ currency }) {
  const [reload, setReload] = useState(0);
  const { loading, error, data, hasLoaded } = useApi(
    `/api/goals?currency=${currency}&reload=${reload}`,
    { goals: [] },
    { keepPreviousData: true }
  );
  const saveScroll = useScrollSaver(loading);

  async function saveGoal(event) {
    event.preventDefault();
    const formElement = event.currentTarget;
    await apiPost("/api/goals/save", Object.fromEntries(new FormData(formElement).entries()));
    formElement.reset();
    saveScroll();
    setReload((value) => value + 1);
  }

  if (loading && !hasLoaded) return <LoadingCard />;
  return (
    <>
      <ErrorCard message={error} />
      <Card title="Add or update a goal">
        <form className="inline-form" onSubmit={saveGoal}>
          <input name="name" placeholder="Goal name" required />
          <input name="target_amount" placeholder="Target" required />
          <input name="saved_amount" placeholder="Saved" required />
          <select name="currency" defaultValue={currency}><option>NZD</option><option>USD</option></select>
          <input type="date" name="target_date" />
          <button className="primary">Save goal</button>
        </form>
      </Card>
      <div className="card-grid">
        {data.goals.map((goal) => <Card key={goal.id} title={goal.name} note={`${goal.percent_complete}% complete`}>
          <ProgressLine amount={goal.saved_minor} total={goal.target_minor} tone="income" />
          <div className="summary-line"><span>Saved</span><strong>{moneyFromMinor(goal.saved_display_minor, currency)}</strong></div>
          <div className="summary-line"><span>Target</span><strong>{moneyFromMinor(goal.target_display_minor, currency)}</strong></div>
        </Card>)}
      </div>
    </>
  );
}

function Investments({ currency }) {
  const [chartType, setChartType] = useState("donut");
  const [reload, setReload] = useState(0);
  const { loading, error, data, hasLoaded } = useApi(
    `/api/investments?currency=${currency}&reload=${reload}`,
    { holdings: [], investment_accounts: [] },
    { keepPreviousData: true }
  );
  const saveScroll = useScrollSaver(loading);

  async function savePosition(event) {
    event.preventDefault();
    const formElement = event.currentTarget;
    await apiPost("/api/investments/position", Object.fromEntries(new FormData(formElement).entries()));
    formElement.reset();
    saveScroll();
    setReload((value) => value + 1);
  }

  if (loading && !hasLoaded) return <LoadingCard />;

  return (
    <>
      <ErrorCard message={error} />
      <div className="stat-grid">
        <StatCard label="Ticker total" value={moneyFromMinor(data.total_minor, currency)} />
        <StatCard label="Provider holdings" value={String(data.provider_count || 0)} />
        <StatCard label="Manual positions" value={String(data.manual_count || 0)} />
        <StatCard label="Largest holding" value={data.largest || "-"} />
      </div>
      <Card title="Allocation" note={`Grouped by ticker in ${currency}`} action={<ChartTabs value={chartType} onChange={setChartType} />}>
        <CategoryVisual rows={data.holdings || []} total={data.total_minor || 0} currency={currency} type={chartType} tone="allocation" />
      </Card>
      <Card title="Holdings" note="Highest to lowest value">
        <div className="holdings-table">
          <div className="holding-head"><span>Security</span><span>Quantity</span><span>Value</span><span>Weight</span></div>
          {(data.holdings || []).map((holding) => (
            <div className="holding-line" key={holding.ticker}>
              <span><strong>{holding.ticker}</strong><small>{holding.security_name || holding.asset_class}</small></span>
              <span>{holding.quantity}</span>
              <strong>{moneyFromMinor(holding.amount_minor, currency)}</strong>
              <span>{percent(holding.amount_minor, data.total_minor)}</span>
            </div>
          ))}
        </div>
      </Card>
      <Card title="Add manual holding" note="Only use this when Plaid does not provide the ticker">
        <form className="inline-form" onSubmit={savePosition}>
          <input type="hidden" name="currency" value={currency} />
          <select name="account_id" required>
            {(data.investment_accounts || []).map((account) => (
              <option key={account.id} value={account.id}>{account.name}</option>
            ))}
          </select>
          <input name="ticker" placeholder="Ticker" required />
          <input name="security_name" placeholder="Name" />
          <input name="asset_class" placeholder="Asset class" defaultValue="Equity" />
          <input name="quantity" placeholder="Quantity" required />
          <input name="price" placeholder="Price" required />
          <input name="value" placeholder="Value" required />
          <select name="position_currency" defaultValue={currency}><option>NZD</option><option>USD</option></select>
          <input type="date" name="as_of" />
          <button className="primary">Save holding</button>
        </form>
      </Card>
    </>
  );
}

function Rules() {
  const [reload, setReload] = useState(0);
  const { loading, error, data, hasLoaded } = useApi(`/api/rules?reload=${reload}`, {
    rules: [],
    categories: [],
    summary: { needs_review: 0, uncategorized: 0, can_apply: 0, top_uncategorized: [] },
  }, { keepPreviousData: true });
  const saveScroll = useScrollSaver(loading);

  async function addRule(event) {
    event.preventDefault();
    const formElement = event.currentTarget;
    await apiPost("/api/rules/add", Object.fromEntries(new FormData(formElement).entries()));
    formElement.reset();
    saveScroll();
    setReload((value) => value + 1);
  }

  async function applyRules() {
    await apiPost("/api/rules/apply", {});
    saveScroll();
    setReload((value) => value + 1);
  }

  async function deactivate(id) {
    await apiPost("/api/rules/deactivate", { rule_id: id });
    saveScroll();
    setReload((value) => value + 1);
  }

  if (loading && !hasLoaded) return <LoadingCard />;
  const summary = data.summary || { needs_review: 0, uncategorized: 0, can_apply: 0, top_uncategorized: [] };
  return (
    <>
      <ErrorCard message={error} />
      <Card title="Cleanup summary" note="This shows what still needs category review">
        <div className="stat-grid">
          <StatCard label="Needs review" value={summary.needs_review} />
          <StatCard label="Uncategorized" value={summary.uncategorized} />
          <StatCard label="Can apply rules" value={summary.can_apply} tone="positive" />
        </div>
        <button type="button" className="primary" onClick={applyRules}>Apply matching rules</button>
        <div className="simple-table">
          {(summary.top_uncategorized || []).map((row) => (
            <div className="simple-row" key={`${row.label}-${row.last_seen}`}>
              <span><strong>{row.label}</strong><small>{row.count} rows</small></span>
              <span>{moneyFromMinor(row.amount_nzd_minor, "NZD")}</span>
              <span>Last seen {row.last_seen || "-"}</span>
            </div>
          ))}
          {!summary.top_uncategorized?.length && <p>No Uncategorized transactions found.</p>}
        </div>
      </Card>
      <Card title="Add a rule">
        <form className="inline-form" onSubmit={addRule}>
          <input name="match_text" placeholder="Merchant contains" required />
          <GroupedCategorySelect name="category" categories={data.categories} />
          <input name="priority" defaultValue="100" />
          <button className="primary">Save rule</button>
        </form>
      </Card>
      <Card title="Saved rules">
        <div className="simple-table">
          {data.rules.map((rule) => (
            <div className="simple-row" key={rule.id}>
              <span><strong>{rule.match_text}</strong><small>{rule.priority}</small></span>
              <span>{rule.category}</span>
              <button onClick={() => deactivate(rule.id)}>Turn off</button>
            </div>
          ))}
        </div>
      </Card>
    </>
  );
}

function SyncStatus() {
  const { loading, error, data, hasLoaded } = useApi(
    "/api/sync-status",
    { sources: [], checks: [], audits: [], runs: [] },
    { keepPreviousData: true }
  );
  if (loading && !hasLoaded) return <LoadingCard />;

  return (
    <>
      <ErrorCard message={error} />
      <Card title="Refresh audit" note="This checks the handoff from imported history to live bank refreshes without showing private IDs.">
        <div className="audit-grid">
          {data.audits.map((audit) => <SyncAudit key={audit.label} audit={audit} />)}
        </div>
      </Card>
      <Card title="Reliability checks" note="These checks look for duplicate risk and pending rows that may need another refresh.">
        <div className="check-grid">
          {data.checks.map((check) => <SyncCheck key={check.label} check={check} />)}
        </div>
      </Card>
      <Card title="Data sources" note="Counts and dates only. No account IDs, transaction IDs, or tokens are shown here.">
        <div className="sync-grid">
          {data.sources.map((source) => <SyncSource key={source.label} source={source} />)}
        </div>
      </Card>
      <Card title="Recent refresh and import runs" note="This is the local history from automation jobs that record a run.">
        <div className="run-list">
          {data.runs.length === 0 && <p className="muted">No refresh history has been recorded yet.</p>}
          {data.runs.map((run, index) => (
            <div className="run-row" key={`${run.job_name}-${run.started_at}-${index}`}>
              <div>
                <strong>{run.job_name}</strong>
                <p>{run.started_at} {run.finished_at ? `to ${run.finished_at}` : "still running"}</p>
                {run.details && <small>{run.details}</small>}
              </div>
              <span className={`soft-pill ${run.status}`}>{run.status}</span>
            </div>
          ))}
        </div>
      </Card>
    </>
  );
}

function SyncAudit({ audit }) {
  const daysText = audit.days_since_latest === null || audit.days_since_latest === undefined
    ? "unknown"
    : `${audit.days_since_latest} days ago`;

  return (
    <div className="sync-audit">
      <div className="audit-heading">
        <div>
          <h3>{audit.label}</h3>
          <p>{audit.note}</p>
        </div>
        <span className={`soft-pill ${audit.status}`}>{audit.status}</span>
      </div>
      <div className="audit-metrics">
        <span><small>{audit.history_label}</small><strong>{Number(audit.history_rows || 0).toLocaleString()}</strong></span>
        <span><small>{audit.live_label}</small><strong>{Number(audit.live_rows || 0).toLocaleString()}</strong></span>
        <span><small>Import cutoff</small><strong>{audit.history_cutoff || "none"}</strong></span>
        <span><small>Latest saved</small><strong>{audit.latest_saved || "none"}</strong></span>
      </div>
      <div className="audit-foot">
        <span>{Number(audit.live_rows_after_cutoff || 0).toLocaleString()} live rows after cutoff</span>
        <span>{Number(audit.likely_duplicates || 0).toLocaleString()} likely duplicates</span>
        <span>{Number(audit.unmatched_old_rows || 0).toLocaleString()} unmatched old rows</span>
        <span>Latest row: {daysText}</span>
      </div>
    </div>
  );
}

function SyncCheck({ check }) {
  return (
    <div className="sync-check">
      <span className={`soft-pill ${check.status}`}>{check.status}</span>
      <div>
        <strong>{check.label}</strong>
        <p>{check.detail}</p>
      </div>
      <strong>{Number(check.value || 0).toLocaleString()}</strong>
    </div>
  );
}

function SyncSource({ source }) {
  const dateRange = source.first_date && source.last_date
    ? `${source.first_date} to ${source.last_date}`
    : "No dates yet";

  return (
    <div className="sync-source">
      <div>
        <h3>{source.label}</h3>
        <span>{source.type}</span>
      </div>
      <strong>{Number(source.total || 0).toLocaleString()}</strong>
      <small>{source.type === "FX rates" ? "rate dates" : "rows saved"}</small>
      <div className="sync-meta">
        <span>Date range</span><strong>{dateRange}</strong>
        {source.accounts > 0 && <><span>Accounts</span><strong>{source.accounts}</strong></>}
        {source.pending > 0 && <><span>Pending</span><strong>{source.pending}</strong></>}
        {source.needs_review > 0 && <><span>Needs review</span><strong>{source.needs_review}</strong></>}
        <span>Last saved</span><strong>{source.last_saved || "Not yet"}</strong>
      </div>
      <p>{source.note}</p>
    </div>
  );
}

function Settings({ currency, setCurrency }) {
  const [message, setMessage] = useState("");
  const [formError, setFormError] = useState("");
  const [sendingTest, setSendingTest] = useState(false);
  const [settingsReload, setSettingsReload] = useState(0);
  const { loading, error, data, hasLoaded } = useApi(
    `/api/settings?reload=${settingsReload}`,
    { settings: {} },
    { keepPreviousData: true }
  );
  const saveScroll = useScrollSaver(loading);
  const settings = data.settings || {};
  const alertCurrency = settings.daily_alert_currency || currency;
  const alertHours = settings.daily_alert_hours || "24";
  const alertMaxRows = settings.daily_alert_max_rows || "20";
  const preview = useApi(
    `/api/daily-alert-preview?currency=${alertCurrency}&hours=${alertHours}&max_rows=${alertMaxRows}&reload=${settingsReload}`,
    { rows: [] }
  );

  async function saveSettings(event) {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(event.currentTarget).entries());
    try {
      setFormError("");
      await apiPost("/api/settings", values);
      if (values.display_currency) setCurrency(values.display_currency);
      saveScroll();
      setSettingsReload((current) => current + 1);
      setMessage("Settings saved");
    } catch (error) {
      setFormError(error.message);
    }
  }

  async function sendTestEmail() {
    try {
      setFormError("");
      setSendingTest(true);
      const result = await apiPost("/api/settings/test-email", {});
      setMessage(result.message || "Test email sent");
    } catch (error) {
      setFormError(error.message);
    } finally {
      setSendingTest(false);
    }
  }

  if (loading && !hasLoaded) return <LoadingCard />;
  return (
    <>
      <ErrorCard message={error} />
      <ErrorCard message={formError} />
      {message && <section className="card success-card">{message}</section>}
      <Card title="Preferences" note="These do not contain passwords or bank tokens">
        <form className="settings-form" onSubmit={saveSettings}>
          <Field label="Default display currency"><select name="display_currency" defaultValue={settings.display_currency || "NZD"}><option>NZD</option><option>USD</option></select></Field>
          <Field label="Daily transaction alerts"><select name="daily_alert_enabled" defaultValue={settings.daily_alert_enabled || "false"}><option value="false">Off</option><option value="true">On</option></select></Field>
          <Field label="Alert time"><input type="time" name="daily_alert_time" defaultValue={settings.daily_alert_time || "07:00"} /></Field>
          <Field label="Alert currency"><select name="daily_alert_currency" defaultValue={settings.daily_alert_currency || "NZD"}><option>NZD</option><option>USD</option></select></Field>
          <Field label="Lookback hours"><input type="number" min="1" max="168" name="daily_alert_hours" defaultValue={settings.daily_alert_hours || "24"} /></Field>
          <Field label="Transactions shown"><input type="number" min="1" max="50" name="daily_alert_max_rows" defaultValue={settings.daily_alert_max_rows || "20"} /></Field>
          <Field label="Send alert when there are no transactions"><select name="daily_alert_send_if_empty" defaultValue={settings.daily_alert_send_if_empty || "false"}><option value="false">No</option><option value="true">Yes</option></select></Field>
          <div className="settings-actions">
            <button className="primary">Save settings</button>
            <button type="button" onClick={sendTestEmail} disabled={sendingTest}>
              {sendingTest ? "Sending..." : "Send test email"}
            </button>
          </div>
        </form>
        <p className="muted">If I change the alert time, I rerun the Task Scheduler install command so Windows uses the new time.</p>
      </Card>
      <AlertPreview loading={preview.loading} error={preview.error} preview={preview.data} currency={alertCurrency} />
    </>
  );
}

function AlertPreview({ loading, error, preview, currency }) {
  const rows = preview.rows || [];
  const hiddenCount = Math.max(0, Number(preview.transaction_count || 0) - Number(preview.shown_count || 0));
  const status = preview.alert_enabled ? "On" : "Off";

  return (
    <Card title="Daily alert preview" note="Email is sent from PowerShell after the Yahoo app password is saved locally.">
      {loading && <p className="muted">Loading preview.</p>}
      {error && <p className="negative">{error}</p>}
      {!loading && !error && (
        <div className="alert-preview">
          <div className="alert-summary">
            <div><span>Status</span><strong>{status}</strong></div>
            <div><span>New posted</span><strong>{Number(preview.transaction_count || 0).toLocaleString()}</strong></div>
            <div><span>Spending</span><strong className="negative">{moneyFromMinor(preview.spending_minor, currency)}</strong></div>
            <div><span>Income</span><strong className="positive">{moneyFromMinor(preview.income_minor, currency)}</strong></div>
          </div>
          <p className="muted">Looks at posted transactions imported in the last {preview.hours || 24} hours. Pending transactions wait until they post.</p>
          {!rows.length && <p className="muted">No posted transactions would be included right now.</p>}
          <div className="alert-list">
            {rows.map((row, index) => (
              <div className="alert-row" key={`${row.posted_date}-${row.merchant}-${index}`}>
                <div>
                  <strong>{row.merchant}</strong>
                  <p>{row.posted_date} | {row.account} | {row.category}</p>
                </div>
                <strong className={row.direction === "income" ? "positive" : row.direction === "spending" ? "negative" : ""}>{row.display_amount}</strong>
              </div>
            ))}
          </div>
          {hiddenCount > 0 && <p className="muted">{hiddenCount.toLocaleString()} more transactions are not shown in this preview.</p>}
        </div>
      )}
    </Card>
  );
}

function CategorySections({ summary, currency, onRowClick }) {
  return (
    <div className="two-column">
      <Card title="Income">
        <CategoryBars rows={summary.income_by_category || []} total={summary.income_total_minor || 0} currency={currency} tone="income" onRowClick={onRowClick} />
      </Card>
      <Card title="Expenses">
        <CategoryBars rows={summary.spending_by_category || []} total={summary.spending_total_minor || 0} currency={currency} tone="expense" onRowClick={onRowClick} />
      </Card>
    </div>
  );
}

function CategoryVisual({ rows, total, currency, type, tone = "expense", onRowClick }) {
  if (!rows.length) return <p className="muted">No matching data found.</p>;
  if (type === "bars") return <CategoryBars rows={rows} total={total} currency={currency} tone={tone} onRowClick={onRowClick} />;
  return <DonutChart rows={rows} total={total} currency={currency} tone={tone} onRowClick={onRowClick} />;
}

function CategoryTooltip({ row, total, currency, tone = "income", totalLabel = "total" }) {
  if (!row) return null;
  return (
    <div className="visual-tooltip">
      <strong>{row.category_name}</strong>
      <span className={tone}>{moneyFromMinor(row.amount_minor, currency)}</span>
      <small>{percent(row.amount_minor, total)} of {totalLabel}</small>
    </div>
  );
}

function CategoryBars({ rows, total, currency, tone = "expense", onRowClick }) {
  const [activeRow, setActiveRow] = useState(null);
  const largestAmount = Math.max(...rows.map((row) => Math.abs(Number(row.amount_minor || 0))), 1);
  return (
    <div className="bar-list" onMouseLeave={() => setActiveRow(null)}>
      <CategoryTooltip row={activeRow} total={total} currency={currency} tone={tone} />
      {rows.map((row, index) => (
        <div
          className={`bar-row ${onRowClick ? "clickable-chart-row" : ""}`}
          key={`${row.category_name}-${index}`}
          onClick={() => onRowClick?.(row)}
          onKeyDown={(event) => {
            if (onRowClick && (event.key === "Enter" || event.key === " ")) {
              event.preventDefault();
              onRowClick(row);
            }
          }}
          onMouseEnter={() => setActiveRow(row)}
          role={onRowClick ? "button" : undefined}
          tabIndex={onRowClick ? 0 : undefined}
        >
          <span>{row.category_name}</span>
          <div><span style={{ width: `${row.amount_minor ? Math.max(2, Math.min(100, Math.abs(row.amount_minor) / largestAmount * 100)) : 0}%` }} className={tone} /></div>
          <strong className="bar-money">
            <span>{moneyFromMinor(row.amount_minor, currency)}</span>
            <small>{percent(row.amount_minor, total)}</small>
          </strong>
        </div>
      ))}
    </div>
  );
}

function DonutChart({ rows, total, currency, tone = "expense", onRowClick }) {
  const [activeRow, setActiveRow] = useState(null);
  useEffect(() => setActiveRow(null), [rows, total]);
  const positiveRows = rows.filter((row) => Number(row.amount_minor || 0) > 0);
  const creditTotal = rows
    .filter((row) => Number(row.amount_minor || 0) < 0)
    .reduce((sum, row) => sum + Number(row.amount_minor || 0), 0);
  const chartRows = positiveRows.slice(0, 9);
  const otherAmount = positiveRows.slice(9).reduce((sum, row) => sum + Number(row.amount_minor || 0), 0);
  if (otherAmount) chartRows.push({ category_name: "Other", amount_minor: otherAmount, aggregated: true });
  const grossTotal = chartRows.reduce((sum, row) => sum + Number(row.amount_minor || 0), 0);
  const safeTotal = Math.max(grossTotal, 1);
  const legendRows = creditTotal
    ? [...chartRows, { category_name: "Credits / refunds", amount_minor: creditTotal, aggregated: true, credit: true }]
    : chartRows;
  const radius = 84;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;
  const segments = chartRows.map((row, index) => {
    const dash = Math.abs(row.amount_minor) / safeTotal * circumference;
    const segment = {
      row,
      dash,
      offset,
      color: `var(--chart-${index % 10})`,
    };
    offset += dash;
    return segment;
  });

  return (
    <div className="donut-layout" onMouseLeave={() => setActiveRow(null)}>
      <div className="donut">
        <svg viewBox="0 0 220 220" role="img" aria-label={`Category breakdown. Net total ${moneyFromMinor(total, currency)}.`}>
          <circle className="donut-track" cx="110" cy="110" r={radius} />
          {segments.map((segment, index) => (
            <circle
              key={`${segment.row.category_name}-${index}`}
              className="donut-segment"
              cx="110"
              cy="110"
              r={radius}
              stroke={segment.color}
              strokeDasharray={`${segment.dash} ${circumference - segment.dash}`}
              strokeDashoffset={-segment.offset}
              onClick={() => !segment.row.aggregated && onRowClick?.(segment.row)}
              onMouseEnter={() => setActiveRow(segment.row)}
            />
          ))}
        </svg>
        <CategoryTooltip row={activeRow} total={grossTotal} currency={currency} tone={activeRow?.credit ? "expense" : tone} totalLabel="gross" />
        <div className="donut-center">
          <strong title={moneyAmountOnly(total)}>{compactMoneyAmount(total)}</strong>
          <small>Net total</small>
        </div>
      </div>
      <div className="donut-legend">
        {legendRows.map((row, index) => (
          <div
            className={onRowClick && !row.aggregated ? "clickable-chart-row" : ""}
            key={`${row.category_name}-${index}`}
            onClick={() => !row.aggregated && onRowClick?.(row)}
            onKeyDown={(event) => {
              if (onRowClick && !row.aggregated && (event.key === "Enter" || event.key === " ")) {
                event.preventDefault();
                onRowClick(row);
              }
            }}
            onMouseEnter={() => setActiveRow(row)}
            onFocus={() => setActiveRow(row)}
            onBlur={() => setActiveRow(null)}
            role={onRowClick && !row.aggregated ? "button" : undefined}
            tabIndex={onRowClick && !row.aggregated ? 0 : undefined}
          >
            <span className="legend-dot" style={{ background: row.credit ? "var(--red)" : `var(--chart-${index % 10})` }} />
            <strong>{row.category_name}</strong>
            <small>{moneyFromMinor(row.amount_minor, currency)} | {percent(row.amount_minor, grossTotal)} of gross</small>
          </div>
        ))}
      </div>
    </div>
  );
}

function ComboChart({ series, currency, onRowClick }) {
  const [hovered, setHovered] = useState(null);
  useEffect(() => setHovered(null), [series, currency]);
  if (!series.length) return <p className="muted">No chart data yet.</p>;
  const chartNumber = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
  const incomeValue = (row) => chartNumber(row.income ?? 0);
  const expenseValue = (row) => chartNumber(row.spending ?? 0);
  const netValue = (row) => chartNumber(row.net ?? 0);
  const baseline = 125;
  const chartHeight = 78;
  const maxValue = Math.max(...series.flatMap((row) => [Math.abs(incomeValue(row)), Math.abs(expenseValue(row)), Math.abs(netValue(row))]), 1);
  const pointSpacing = 600 / Math.max(series.length - 1, 1);
  const xForIndex = (index) => 70 + index * pointSpacing;
  const yForValue = (value) => baseline - (value / maxValue) * chartHeight;
  const tickValues = Array.from(new Set([maxValue, Math.round(maxValue / 2), 0, -Math.round(maxValue / 2), -maxValue]));
  const pointRows = series.map((row, index) => ({ row, index, x: xForIndex(index), y: yForValue(netValue(row)) }));
  const points = pointRows.map(({ x, y }) => `${x},${y}`).join(" ");
  const firstDate = series[0]?.date ? new Date(`${series[0].date}T00:00:00`) : null;
  const lastDate = series[series.length - 1]?.date ? new Date(`${series[series.length - 1].date}T00:00:00`) : null;
  const includeYear = firstDate && lastDate && (lastDate - firstDate) > 365 * 24 * 60 * 60 * 1000;
  const labelEvery = Math.max(1, Math.ceil(series.length / 8));
  const labelPoints = pointRows.filter(({ index }) => index % labelEvery === 0 || index === pointRows.length - 1);
  if (labelPoints.length > 1 && labelPoints[labelPoints.length - 1].x - labelPoints[labelPoints.length - 2].x < 95) {
    labelPoints.splice(labelPoints.length - 2, 1);
  }
  const groupWidth = Math.max(2, Math.min(16, pointSpacing * 0.72));
  const barGap = Math.min(2, groupWidth * 0.2);
  const barWidth = Math.max(1, (groupWidth - barGap) / 2);
  const tooltipX = hovered ? Math.min(Math.max(hovered.x - 70, 70), 570) : 70;

  function selectIndex(index) {
    const safeIndex = Math.max(0, Math.min(series.length - 1, index));
    const point = pointRows[safeIndex];
    setHovered({
      index: safeIndex,
      x: point.x,
      y: point.y,
      title: point.row.label || point.row.date || `Period ${safeIndex + 1}`,
      lines: [
        `Income ${moneyFromMinor(incomeValue(point.row), currency)}`,
        `Expenses ${moneyFromMinor(expenseValue(point.row), currency)}`,
        `Net ${moneyFromMinor(netValue(point.row), currency)}`,
      ],
    });
    return safeIndex;
  }

  function indexFromPointer(event) {
    const svg = event.currentTarget.ownerSVGElement;
    const bounds = svg.getBoundingClientRect();
    const viewX = (event.clientX - bounds.left) / Math.max(bounds.width, 1) * 740;
    return Math.round((viewX - 70) / pointSpacing);
  }

  function handleChartKey(event) {
    const current = hovered?.index ?? series.length - 1;
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? series.length - 1 : current + (event.key === "ArrowLeft" ? -1 : 1);
      selectIndex(next);
    } else if (onRowClick && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      onRowClick(series[current]);
    }
  }

  return (
    <div className="combo-wrap">
      <svg viewBox="0 0 740 250" role="group" aria-label="Cash flow timeline chart" onMouseLeave={() => setHovered(null)}>
        {tickValues.map((value) => {
          const y = yForValue(value);
          return (
            <g key={value}>
              <line x1="60" y1={y} x2="715" y2={y} className={value === 0 ? "axis" : "grid-line"} />
              <text x="50" y={y + 4} className="axis-label">{chartAxisMoney(value, tickValues)}</text>
            </g>
          );
        })}
        {pointRows.map(({ row, index, x }) => {
          const incomeY = yForValue(incomeValue(row));
          const expenseY = yForValue(-expenseValue(row));
          return (
            <g key={`${row.label}-${index}`}>
              <rect x={x - groupWidth / 2} y={Math.min(baseline, incomeY)} width={barWidth} height={Math.abs(baseline - incomeY)} className="income-bar" />
              <rect x={x - groupWidth / 2 + barWidth + barGap} y={Math.min(baseline, expenseY)} width={barWidth} height={Math.abs(baseline - expenseY)} className="expense-bar" />
            </g>
          );
        })}
        {labelPoints.map(({ row, x }, index) => (
          <text key={`${row.label}-${x}`} x={x} y="225" style={{ textAnchor: index === 0 ? "start" : index === labelPoints.length - 1 ? "end" : "middle" }}>{chartDateLabel(row, includeYear)}</text>
        ))}
        <polyline points={points} className="net-line" />
        {pointRows.length === 1 && <circle cx={pointRows[0].x} cy={pointRows[0].y} r="4" className="chart-point" />}
        <rect
          x="60"
          y="28"
          width="655"
          height="178"
          className={`hover-zone chart-interaction ${onRowClick ? "clickable-chart-zone" : ""}`}
          aria-label={`Cash flow chart with ${series.length} periods. Use left and right arrows to inspect values${onRowClick ? ", then Enter to open transactions" : ""}.`}
          onClick={(event) => {
            const index = selectIndex(indexFromPointer(event));
            onRowClick?.(series[index]);
          }}
          onFocus={() => selectIndex(hovered?.index ?? series.length - 1)}
          onKeyDown={handleChartKey}
          onMouseMove={(event) => selectIndex(indexFromPointer(event))}
          onBlur={() => setHovered(null)}
          role={onRowClick ? "button" : undefined}
          tabIndex="0"
        />
        {hovered && (
          <g className="chart-tooltip">
            <line x1={hovered.x} y1="32" x2={hovered.x} y2="202" className="tooltip-line" />
            <rect x={tooltipX} y="18" width="150" height="74" rx="10" />
            <text x={tooltipX + 10} y="39" className="tooltip-title">{hovered.title}</text>
            {hovered.lines.map((line, index) => (
              <text key={line} x={tooltipX + 10} y={58 + index * 15}>{line}</text>
            ))}
          </g>
        )}
      </svg>
      <div className="chart-legend"><span className="income-dot" /> Income <span className="expense-dot" /> Expenses <span className="net-dot" /> Net</div>
    </div>
  );
}

function LineChart({ series, currency, onRowClick }) {
  const [hovered, setHovered] = useState(null);
  useEffect(() => setHovered(null), [series, currency]);
  if (!series.length) return <p className="muted">No history yet.</p>;
  const values = series.map((row) => Number.isFinite(Number(row.value_minor)) ? Number(row.value_minor) : 0);
  const axis = chartAxisRange(values, 5);
  const lower = axis.lower;
  const upper = axis.upper;
  const range = Math.max(upper - lower, 1);
  const pointSpacing = 600 / Math.max(series.length - 1, 1);
  const xForIndex = (index) => 70 + index * pointSpacing;
  const yForValue = (value) => 185 - ((value - lower) / range) * 140;
  const pointRows = series.map((row, index) => ({
    row,
    x: xForIndex(index),
    y: yForValue(values[index]),
  }));
  const points = pointRows.map((point) => `${point.x},${point.y}`).join(" ");
  const tickValues = axis.ticks;
  const firstDate = series[0]?.date ? new Date(`${series[0].date}T00:00:00`) : null;
  const lastDate = series[series.length - 1]?.date ? new Date(`${series[series.length - 1].date}T00:00:00`) : null;
  const includeYear = firstDate && lastDate && (lastDate - firstDate) > 365 * 24 * 60 * 60 * 1000;
  const labelEvery = Math.max(1, Math.ceil(series.length / 6));
  const labelPoints = [];
  const firstPoint = pointRows[0];
  const lastPoint = pointRows[pointRows.length - 1];
  const tooltipX = hovered ? Math.min(Math.max(hovered.x - 70, 70), 570) : 70;

  pointRows.forEach((point, index) => {
    if (index % labelEvery === 0 || index === pointRows.length - 1) {
      labelPoints.push(point);
    }
  });

  if (labelPoints.length > 1) {
    const lastLabel = labelPoints[labelPoints.length - 1];
    const previousLabel = labelPoints[labelPoints.length - 2];
    if (lastLabel.x - previousLabel.x < 95) {
      labelPoints.splice(labelPoints.length - 2, 1);
    }
  }

  function selectIndex(index) {
    const safeIndex = Math.max(0, Math.min(pointRows.length - 1, index));
    const point = pointRows[safeIndex];
    setHovered({
      index: safeIndex,
      x: point.x,
      y: point.y,
      title: point.row.date || point.row.label || `Point ${safeIndex + 1}`,
      lines: [`Net worth ${moneyFromMinor(values[safeIndex], currency)}`],
    });
    return safeIndex;
  }

  function indexFromPointer(event) {
    const svg = event.currentTarget.ownerSVGElement;
    const bounds = svg.getBoundingClientRect();
    const viewX = (event.clientX - bounds.left) / Math.max(bounds.width, 1) * 740;
    return Math.round((viewX - 70) / pointSpacing);
  }

  function handleChartKey(event) {
    const current = hovered?.index ?? pointRows.length - 1;
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? pointRows.length - 1 : current + (event.key === "ArrowLeft" ? -1 : 1);
      selectIndex(next);
    } else if (onRowClick && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      onRowClick(pointRows[current].row);
    }
  }

  return (
    <div className="combo-wrap">
      <svg viewBox="0 0 740 235" role="group" aria-label="Net worth timeline chart" onMouseLeave={() => setHovered(null)}>
        {tickValues.map((value) => {
          const y = yForValue(value);
          return (
            <g key={value}>
              <line x1="60" y1={y} x2="715" y2={y} className="grid-line" />
              <text x="50" y={y + 4} className="axis-label">{chartAxisMoney(value, tickValues)}</text>
            </g>
          );
        })}
        {labelPoints.map(({ row, x }, index) => (
          <text
            key={`${row.label}-${x}`}
            x={index === 0 ? Math.max(x, 70) : x}
            y="222"
            style={{ textAnchor: index === 0 ? "start" : index === labelPoints.length - 1 ? "end" : "middle" }}
          >
            {chartDateLabel(row, includeYear)}
          </text>
        ))}
        <polygon points={`${points} ${lastPoint.x},190 ${firstPoint.x},190`} className="area-fill" />
        <polyline points={points} className="area-line" />
        {pointRows.length === 1 && <circle cx={firstPoint.x} cy={firstPoint.y} r="4" className="chart-point" />}
        <rect
          x="60"
          y="35"
          width="655"
          height="160"
          className={`hover-zone chart-interaction ${onRowClick ? "clickable-chart-zone" : ""}`}
          aria-label={`Net worth chart with ${pointRows.length} points. Use left and right arrows to inspect values${onRowClick ? ", then Enter to open transactions" : ""}.`}
          onClick={(event) => {
            const index = selectIndex(indexFromPointer(event));
            onRowClick?.(pointRows[index].row);
          }}
          onFocus={() => selectIndex(hovered?.index ?? pointRows.length - 1)}
          onKeyDown={handleChartKey}
          onMouseMove={(event) => selectIndex(indexFromPointer(event))}
          onBlur={() => setHovered(null)}
          role={onRowClick ? "button" : undefined}
          tabIndex="0"
        />
        {hovered && (
          <g className="chart-tooltip">
            <line x1={hovered.x} y1="35" x2={hovered.x} y2="195" className="tooltip-line" />
            <rect x={tooltipX} y="24" width="150" height="54" rx="10" />
            <text x={tooltipX + 10} y="45" className="tooltip-title">{hovered.title}</text>
            <text x={tooltipX + 10} y="64">{hovered.lines[0]}</text>
          </g>
        )}
      </svg>
      <div className="chart-legend">Latest: {moneyFromMinor(values[values.length - 1], currency)}</div>
    </div>
  );
}

function ProgressLine({ amount, total, tone = "income" }) {
  const value = Math.min(100, Math.abs(amount || 0) / Math.max(Math.abs(total || 0), 1) * 100);
  return <div className="progress-line" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={Math.round(value)}><span className={tone} style={{ width: `${value}%` }} /></div>;
}

function TimelineTabs({ value, onChange }) {
  const items = [
    ["1m", "This month"],
    ["3m", "3M"],
    ["6m", "6M"],
    ["1y", "1Y"],
    ["3y", "3Y"],
    ["5y", "5Y"],
    ["all", "All"],
  ];
  return (
    <label className="timeline-control">
      <select value={value} onChange={(event) => onChange(event.target.value)} aria-label="Timeline">
        {items.map(([item, label]) => <option key={item} value={item}>{label}</option>)}
      </select>
    </label>
  );
}

function ChartTabs({ value, onChange, includeCombo = false }) {
  const items = includeCombo ? ["donut", "bars", "combo"] : ["donut", "bars"];
  return <div className="segmented">{items.map((item) => <button key={item} className={value === item ? "active" : ""} onClick={() => onChange(item)}>{item[0].toUpperCase() + item.slice(1)}</button>)}</div>;
}

function Field({ label, children }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}

function Select({ value, onChange, options, emptyLabel }) {
  return <select value={value} onChange={(event) => onChange(event.target.value)}>{options.map((option) => <option key={option || "empty"} value={option}>{option || emptyLabel}</option>)}</select>;
}

function GroupedCategorySelect({ value = "", onChange, categories = [], compact = false, name = "", emptyLabel = "All categories" }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [localValue, setLocalValue] = useState(value);
  const [menuStyle, setMenuStyle] = useState({});
  const buttonRef = useRef(null);
  const selectedValue = onChange ? value : localValue;
  const grouped = useMemo(() => {
    const groups = [];
    categories.forEach((category) => {
      let group = groups.find((item) => item.name === category.group_name);
      if (!group) {
        group = { name: category.group_name, rows: [] };
        groups.push(group);
      }
      group.rows.push(category);
    });
    return groups;
  }, [categories]);
  const cleanSearch = search.trim().toLowerCase();
  const filteredGroups = grouped
    .map((group) => ({
      ...group,
      rows: group.rows.filter((category) => category.name.toLowerCase().includes(cleanSearch)),
    }))
    .filter((group) => group.rows.length);
  const matchingRows = filteredGroups.flatMap((group) => group.rows);

  function positionMenu() {
    if (!buttonRef.current) return;

    const button = buttonRef.current.getBoundingClientRect();
    const menuWidth = Math.max(button.width, compact ? 260 : 280);
    const left = Math.min(Math.max(12, button.left), window.innerWidth - menuWidth - 12);
    const spaceBelow = window.innerHeight - button.bottom;
    const spaceAbove = button.top;
    const openUp = spaceBelow < 280 && spaceAbove > spaceBelow;
    const room = openUp ? spaceAbove : spaceBelow;
    const maxHeight = Math.max(180, Math.min(320, room - 18));
    const top = openUp ? Math.max(12, button.top - maxHeight - 8) : button.bottom + 8;

    setMenuStyle({
      left: `${left}px`,
      maxHeight: `${maxHeight}px`,
      top: `${top}px`,
      width: `${menuWidth}px`,
    });
  }

  useEffect(() => {
    if (!open) return undefined;

    positionMenu();
    window.addEventListener("resize", positionMenu);
    window.addEventListener("scroll", positionMenu, true);

    return () => {
      window.removeEventListener("resize", positionMenu);
      window.removeEventListener("scroll", positionMenu, true);
    };
  }, [open, compact]);

  function chooseCategory(categoryName) {
    if (onChange) {
      onChange(categoryName);
    } else {
      setLocalValue(categoryName);
    }
    setSearch("");
    setOpen(false);
  }

  return (
    <div
      className={`category-picker ${compact ? "compact-select" : ""}`}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      {name && <input type="hidden" name={name} value={selectedValue} />}
      <button type="button" ref={buttonRef} className="category-picker-button" onClick={() => {
        setOpen((current) => !current);
        setSearch("");
      }}>
        <span>{selectedValue || emptyLabel}</span>
      </button>
      {open && (
        <div className="category-picker-menu" style={menuStyle}>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && matchingRows.length) {
                event.preventDefault();
                chooseCategory(matchingRows[0].name);
              }
              if (event.key === "Escape") setOpen(false);
            }}
            placeholder="Type a category"
            autoFocus
          />
          <button type="button" className="category-picker-option" onMouseDown={(event) => event.preventDefault()} onClick={() => chooseCategory("")}>
            {emptyLabel}
          </button>
          {filteredGroups.map((group) => (
            <div className="category-picker-group" key={group.name}>
              <strong>{group.name}</strong>
              {group.rows.map((category) => (
                <button
                  type="button"
                  className={category.name === selectedValue ? "category-picker-option selected" : "category-picker-option"}
                  key={category.name}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => chooseCategory(category.name)}
                >
                  {category.name}
                </button>
              ))}
            </div>
          ))}
          {!matchingRows.length && <p className="muted">No matching categories.</p>}
        </div>
      )}
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
