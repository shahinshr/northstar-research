"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Bell, FlaskConical, GitCompareArrows, LayoutDashboard, Loader2, Moon, Settings, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";

const LINKS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/compare", label: "Compare", icon: GitCompareArrows },
  { href: "/validation", label: "Validation", icon: FlaskConical },
  { href: "/data-health", label: "Data health", icon: Activity },
  { href: "/settings", label: "Settings", icon: Settings },
];

/** Live indicator: polls a tiny status endpoint and refreshes page data when a new snapshot is published. */
function LiveStatus() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 20_000, refetchOnWindowFocus: true });
  const lastRun = useRef<string | null>(null);
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick((x) => x + 1), 30_000); return () => clearInterval(t); }, []);
  useEffect(() => {
    const id = q.data?.run_id ?? null;
    if (id && lastRun.current && id !== lastRun.current) {
      qc.invalidateQueries({ predicate: (x) => !["status", "job"].includes(x.queryKey[0] as string) });
    }
    lastRun.current = id;
  }, [q.data?.run_id, qc]);
  const d = q.data;
  if (!d) return null;
  const busy = d.active_job && ["queued", "fetching", "calculating"].includes(d.active_job.status);
  const open = d.markets_open.length;
  return (
    <div className="hidden items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 text-xs text-ink2 md:flex"
      title={`Auto-refresh: intraday quotes & breaking news every ${d.intraday_minutes} min while markets are open; full analysis daily. Markets open: ${d.markets_open.join(", ") || "none"}`}>
      {busy ? <Loader2 size={12} className="animate-spin text-accent" aria-hidden />
        : <span className={open ? "live-dot" : "inline-block h-2 w-2 rounded-full bg-[var(--muted)]"} aria-hidden />}
      <span className="font-medium text-ink">{busy ? "Updating" : open ? "Live" : "Markets closed"}</span>
      <span className="text-muted">· snapshot {ago(d.run_at)}{d.run_kind === "intraday" ? " (intraday)" : ""}</span>
    </div>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark" | null>(null);
  useEffect(() => {
    let t: string | null = null;
    try { t = document.documentElement.dataset.theme ?? localStorage.getItem("theme"); } catch { /* ignore */ }
    setTheme((t as "light" | "dark") ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  }, []);
  const flip = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch { /* ignore */ }
  };
  return (
    <button onClick={flip} className="btn rounded-full px-2" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}>
      {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
    </button>
  );
}

function Alerts() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const q = useQuery({ queryKey: ["alerts"], queryFn: api.alerts, refetchInterval: 60_000 });
  const read = useMutation({ mutationFn: api.readAlerts, onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }) });
  const unread = (q.data ?? []).filter((a) => !a.read).length;
  return (
    <div className="relative">
      <button className="btn relative rounded-full px-2" onClick={() => setOpen(!open)} aria-label={`Alerts (${unread} unread)`} aria-expanded={open}>
        <Bell size={15} />
        {unread ? <span className="absolute -right-1 -top-1 min-w-[18px] rounded-full bg-[var(--bad)] px-1 text-center text-2xs font-semibold text-white">{unread > 99 ? "99+" : unread}</span> : null}
      </button>
      {open ? (
        <div className="animate-in absolute right-0 z-40 mt-2 w-96 overflow-hidden rounded-2xl border border-line bg-raised" style={{ boxShadow: "var(--shadow-lg)" }}>
          <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
            <span className="text-sm font-semibold text-ink">Alerts</span>
            <button className="text-xs text-accent-ink hover:underline" onClick={() => read.mutate()}>Mark all read</button>
          </div>
          <ul className="scroll-thin max-h-96 overflow-auto">
            {(q.data ?? []).length === 0 ? <li className="px-4 py-5 text-sm text-muted">No alerts yet. New candidates, score drops, new earnings and stale watchlist data appear here.</li> : null}
            {(q.data ?? []).map((a) => (
              <li key={a.id} className={`border-b border-line px-4 py-2.5 text-sm last:border-0 ${a.read ? "text-ink2" : "text-ink"}`}>
                <Link href={a.instrument_id ? `/instrument/${a.instrument_id}` : "/"} onClick={() => setOpen(false)} className="hover:underline">{a.message}</Link>
                <div className="text-2xs text-muted">{a.kind.replace("_", " ")} · {ago(a.created_at)}</div>
              </li>))}
          </ul>
        </div>) : null}
    </div>
  );
}

export default function Nav() {
  const path = usePathname();
  return (
    <header className="glass sticky top-0 z-30 border-b border-line">
      <div className="mx-auto flex h-16 max-w-[1600px] items-center gap-5 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2.5 font-semibold tracking-tight text-ink">
          <span className="grid h-8 w-8 place-items-center rounded-xl text-white" style={{ background: "linear-gradient(135deg, var(--accent), var(--accent-2))" }}>
            <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden><path d="M3 17l5-5 4 4 8-9" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
          </span>
          <span className="text-[15px]">Northstar <span className="font-normal text-muted">Research</span></span>
        </Link>
        <nav className="flex items-center gap-0.5 rounded-xl bg-sunken p-1" aria-label="Main">
          {LINKS.map(({ href, label, icon: Icon }) => {
            const on = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link key={href} href={href} aria-current={on ? "page" : undefined}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition-colors ${on ? "bg-surface font-medium text-ink" : "text-ink2 hover:text-ink"}`}
                style={on ? { boxShadow: "var(--shadow-sm)" } : undefined}>
                <Icon size={15} aria-hidden /> <span className="hidden lg:inline">{label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <LiveStatus />
          <Alerts />
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
