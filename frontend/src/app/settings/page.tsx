"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Note, Skeleton } from "@/components/ui";
import { api, type Settings } from "@/lib/api";

const ZONES = ["America/Toronto", "America/Vancouver", "America/New_York", "Europe/London", "UTC"];

export default function SettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: api.settings });
  const [form, setForm] = useState<Settings | null>(null);
  useEffect(() => { if (q.data) setForm(q.data); }, [q.data]);
  const save = useMutation({ mutationFn: (b: Partial<Settings>) => api.saveSettings(b),
    onSuccess: (d) => { qc.setQueryData(["settings"], d); }, onError: (e: Error) => alert(e.message) });
  if (!form) return <Skeleton className="h-64" />;
  return (
    <div className="max-w-2xl space-y-5">
      <h1 className="text-2xl font-semibold tracking-tight text-ink">Settings</h1>
      <form className="card space-y-4 p-5" onSubmit={(e) => { e.preventDefault(); save.mutate(form); }}>
        <fieldset>
          <legend className="label mb-2">Stock universe</legend>
          {([["starter", "Starter — 17 hand-picked stocks (fast, for testing)"],
            ["core", "Core — S&P 500 + S&P/TSX Composite + FTSE 100 (~820 stocks)"],
            ["extended", "Extended — core + FTSE 250 (~1,070 stocks)"]] as const).map(([k, l]) => (
            <label key={k} className="flex items-center gap-2 py-1 text-sm text-ink">
              <input type="radio" name="universe" checked={form.universe === k} onChange={() => setForm({ ...form, universe: k })} />{l}
            </label>))}
          <p className="mt-1 text-xs text-muted">The 64-fund catalogue and your watchlist/added tickers are always included. Changes apply on the next discovery run.</p>
        </fieldset>
        <div className="grid grid-cols-2 gap-4">
          <label className="text-sm text-ink"><span className="label mb-1 block">Daily discovery time</span>
            <input type="time" className="input w-full" value={form.discovery_time} onChange={(e) => setForm({ ...form, discovery_time: e.target.value })} /></label>
          <label className="text-sm text-ink"><span className="label mb-1 block">Time zone (IANA)</span>
            <select className="input w-full" value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })}>
              {[...new Set([form.timezone, ...ZONES])].map((z) => <option key={z}>{z}</option>)}
            </select></label>
        </div>
        <p className="text-xs text-muted">Wall-clock schedule (not a 24-hour interval), so it stays correct across daylight-saving changes. Presentation currency: {form.presentation_currency}.</p>
        <button className="btn-primary" disabled={save.isPending}>{save.isPending ? "Saving…" : "Save settings"}</button>
        {save.isSuccess ? <span className="ml-3 text-sm text-up">Saved</span> : null}
      </form>
      <Note>API keys live server-side in <code>backend/.env</code> (never in the browser). Provider status and sign-up links are on the Data health page.</Note>
    </div>
  );
}
