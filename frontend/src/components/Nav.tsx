"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Bell, FlaskConical, GitCompareArrows, LayoutDashboard, Moon, Settings, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";

const LINKS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/compare", label: "Compare", icon: GitCompareArrows },
  { href: "/validation", label: "Validation", icon: FlaskConical },
  { href: "/data-health", label: "Data health", icon: Activity },
  { href: "/settings", label: "Settings", icon: Settings },
];

function Alerts() {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const q = useQuery({ queryKey: ["alerts"], queryFn: api.alerts, refetchInterval: 60_000 });
  const read = useMutation({ mutationFn: api.readAlerts, onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }) });
  const unread = (q.data ?? []).filter((a) => !a.read).length;
  return (
    <div className="relative">
      <button className="btn relative px-2" onClick={() => setOpen(!open)} aria-label={`Alerts (${unread} unread)`} aria-expanded={open}>
        <Bell size={15} />
        {unread ? <span className="absolute -right-1 -top-1 rounded-full bg-[var(--bad)] px-1 text-2xs font-semibold text-white">{unread}</span> : null}
      </button>
      {open ? (
        <div className="absolute right-0 z-40 mt-1 w-96 rounded-lg border border-line bg-raised shadow-xl">
          <div className="flex items-center justify-between border-b border-line px-3 py-2">
            <span className="text-sm font-semibold text-ink">Alerts</span>
            <button className="text-xs text-accent-ink hover:underline" onClick={() => read.mutate()}>Mark all read</button>
          </div>
          <ul className="max-h-96 overflow-auto">
            {(q.data ?? []).length === 0 ? <li className="px-3 py-4 text-sm text-muted">No alerts yet. They appear after the second discovery run (new candidates, score drops, new earnings, stale watchlist data).</li> : null}
            {(q.data ?? []).map((a) => (
              <li key={a.id} className={`border-b border-line px-3 py-2 text-sm last:border-0 ${a.read ? "text-ink2" : "text-ink"}`}>
                <Link href={a.instrument_id ? `/instrument/${a.instrument_id}` : "/"} onClick={() => setOpen(false)} className="hover:underline">{a.message}</Link>
                <div className="text-2xs text-muted">{a.kind.replace("_", " ")} · {ago(a.created_at)}</div>
              </li>))}
          </ul>
        </div>) : null}
    </div>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark" | null>(null);
  useEffect(() => {
    let t: string | null = null;
    try { t = localStorage.getItem("theme"); } catch { /* ignore */ }
    const initial = (t as "light" | "dark") ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    setTheme(initial);
    if (t) document.documentElement.dataset.theme = t;
  }, []);
  const flip = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch { /* ignore */ }
  };
  return (
    <button onClick={flip} className="btn px-2" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}>
      {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
    </button>
  );
}

export default function Nav() {
  const path = usePathname();
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-surface">
      <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-6 px-4">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight text-ink">
          <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden>
            <rect x="2" y="13" width="4" height="9" rx="1.5" fill="var(--s1)" />
            <rect x="10" y="8" width="4" height="14" rx="1.5" fill="var(--s3)" />
            <rect x="18" y="3" width="4" height="19" rx="1.5" fill="var(--s2)" />
          </svg>
          <span>Northstar <span className="font-normal text-muted">Research</span></span>
        </Link>
        <nav className="flex items-center gap-1" aria-label="Main">
          {LINKS.map(({ href, label, icon: Icon }) => {
            const on = href === "/" ? path === "/" : path.startsWith(href);
            return (
              <Link key={href} href={href} aria-current={on ? "page" : undefined}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm ${on ? "bg-page font-medium text-ink" : "text-ink2 hover:text-ink"}`}>
                <Icon size={15} aria-hidden /> <span className="hidden sm:inline">{label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <span className="hidden text-xs text-muted xl:inline">Research shortlist · not investment advice</span>
          <Alerts />
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
