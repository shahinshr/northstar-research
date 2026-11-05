"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleAlert, Loader2, XCircle } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api, type Job } from "@/lib/api";

const KEY = "active-job-id";
const ACTIVE = new Set(["queued", "fetching", "calculating"]);

/** Tracks one background job. The id is kept in localStorage so a browser reload never loses job state. */
export function useJobTracker() {
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);

  useEffect(() => {
    try { setJobId(localStorage.getItem(KEY)); } catch { /* storage unavailable */ }
  }, []);

  const q = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.job(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => (query.state.data && !ACTIVE.has(query.state.data.status) ? false : 1500),
  });

  useEffect(() => {
    if (q.data && !ACTIVE.has(q.data.status)) {
      qc.invalidateQueries({ predicate: (x) => x.queryKey[0] !== "job" });
    }
  }, [q.data?.status, qc]); // eslint-disable-line react-hooks/exhaustive-deps

  const track = useCallback((job: Job) => {
    setJobId(job.job_id);
    try { localStorage.setItem(KEY, job.job_id); } catch { /* ignore */ }
    qc.setQueryData(["job", job.job_id], job);
  }, [qc]);

  return { job: q.data ?? null, track, busy: !!q.data && ACTIVE.has(q.data.status) };
}

const LABEL: Record<string, string> = {
  queued: "Queued", fetching: "Fetching data", calculating: "Calculating", completed: "Completed",
  partial: "Completed with gaps", failed: "Failed",
};
const KIND: Record<string, string> = {
  discovery: "Discovery run", refresh_prices: "Price refresh", refresh_analysis: "Analysis refresh", recalculate: "Recalculation",
};

export function JobPill({ job }: { job: Job | null }) {
  if (!job) return null;
  const active = ACTIVE.has(job.status);
  const p = job.progress ?? {};
  const Icon = active ? Loader2 : job.status === "completed" ? CheckCircle2 : job.status === "partial" ? CircleAlert : XCircle;
  const tone = active ? "text-accent-ink" : job.status === "completed" ? "text-up" : job.status === "partial" ? "text-ink2" : "text-down";
  return (
    <div className="flex items-center gap-2 rounded-lg border border-line bg-raised px-2.5 py-1 text-xs" role="status" aria-live="polite">
      <Icon size={14} className={`${tone} ${active ? "animate-spin" : ""}`} aria-hidden />
      <span className="font-medium text-ink">{KIND[job.kind] ?? job.kind}</span>
      <span className={tone}>{LABEL[job.status]}</span>
      {active && p.requested ? (
        <span className="tnum text-muted">{p.done ?? 0}/{p.requested}{p.current ? ` · ${p.current}` : ""}</span>
      ) : null}
      {!active && job.message ? <span className="hidden max-w-[22rem] truncate text-muted md:inline" title={job.message}>{job.message}</span> : null}
    </div>
  );
}
