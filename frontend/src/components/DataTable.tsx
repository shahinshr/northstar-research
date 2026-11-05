"use client";

import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Columns3, Star } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import type { Row } from "@/lib/api";

export type Col = { id: string; label: string; title?: string; def: boolean; align?: "right";
  sort?: (r: Row) => number | string | null | undefined; cell: (r: Row) => React.ReactNode };

export function usePersisted<T>(key: string, init: T): [T, (v: T) => void] {
  const [s, set] = useState<T>(init);
  useEffect(() => { try { const x = localStorage.getItem(key); if (x) set(JSON.parse(x)); } catch { /* ignore */ } }, [key]);
  return [s, (val: T) => { set(val); try { localStorage.setItem(key, JSON.stringify(val)); } catch { /* ignore */ } }];
}

export function ColumnPicker({ cols, visible, setVisible }: { cols: Col[]; visible: string[]; setVisible: (v: string[]) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button className="btn" onClick={() => setOpen(!open)} aria-expanded={open}><Columns3 size={14} /> Columns</button>
      {open ? (
        <div className="absolute right-0 z-30 mt-1 grid w-80 grid-cols-2 gap-1 rounded-lg border border-line bg-raised p-3 shadow-xl">
          {cols.map((x) => (
            <label key={x.id} className="flex items-center gap-2 text-xs text-ink2">
              <input type="checkbox" checked={visible.includes(x.id)}
                onChange={(e) => setVisible(e.target.checked ? [...visible, x.id] : visible.filter((y) => y !== x.id))} />{x.label}
            </label>))}
          <button className="col-span-2 mt-1 text-left text-xs text-accent-ink hover:underline"
            onClick={() => setVisible(cols.filter((x) => x.def).map((x) => x.id))}>Reset to default</button>
        </div>) : null}
    </div>
  );
}

/** Sortable, paginated table with sticky identity columns, selection and watch toggles (spec §17). */
export function DataTable({ rows, cols, visible, defaultSort, selected, setSelected, onWatch, groupBy, pageSize = 50, empty }: {
  rows: Row[]; cols: Col[]; visible: string[]; defaultSort: { id: string; dir: 1 | -1 };
  selected: string[]; setSelected: (v: string[]) => void; onWatch: (r: Row) => void;
  groupBy?: (r: Row) => string; pageSize?: number; empty: React.ReactNode;
}) {
  const [sort, setSort] = useState(defaultSort);
  const [page, setPage] = useState(0);
  useEffect(() => setPage(0), [rows.length, sort.id, sort.dir]);
  const shown = cols.filter((c) => visible.includes(c.id));
  const sorted = useMemo(() => {
    const col = cols.find((x) => x.id === sort.id);
    const out = [...rows];
    out.sort((a, b) => {
      if (groupBy) {
        const g = groupBy(a).localeCompare(groupBy(b));
        if (g) return g;
      }
      if (!col?.sort) return 0;
      const x = col.sort(a), y = col.sort(b);
      const xn = x === null || x === undefined, yn = y === null || y === undefined;
      if (xn && yn) return a.symbol.localeCompare(b.symbol);
      if (xn) return 1;
      if (yn) return -1;
      return ((x as number) < (y as number) ? -1 : (x as number) > (y as number) ? 1 : 0) * sort.dir;
    });
    return out;
  }, [rows, cols, sort, groupBy]);
  const pages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const slice = sorted.slice(page * pageSize, (page + 1) * pageSize);
  if (!rows.length) return <div className="px-6 py-12 text-center text-sm text-ink2">{empty}</div>;
  let lastGroup = "";
  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1100px] border-collapse text-sm">
          <thead>
            <tr className="border-b border-line text-left">
              <th className="sticky left-0 z-10 w-[64px] min-w-[64px] bg-surface py-2 pl-3 pr-2" scope="col"><span className="sr-only">Select / watch</span></th>
              <th className="sticky left-[64px] z-10 bg-surface px-2 py-2 text-2xs font-semibold uppercase tracking-wide text-muted" scope="col">Instrument</th>
              {shown.map((col) => (
                <th key={col.id} scope="col" title={col.title} aria-sort={sort.id === col.id ? (sort.dir === 1 ? "ascending" : "descending") : "none"}
                  className={`whitespace-nowrap px-2 py-2 text-2xs font-semibold uppercase tracking-wide text-muted ${col.align === "right" ? "text-right" : ""}`}>
                  <button className="inline-flex items-center gap-0.5 hover:text-ink" disabled={!col.sort}
                    onClick={() => setSort({ id: col.id, dir: sort.id === col.id ? (sort.dir === 1 ? -1 : 1) : -1 })}>
                    {col.label}{sort.id === col.id ? (sort.dir === 1 ? <ArrowUp size={11} /> : <ArrowDown size={11} />) : null}
                  </button>
                </th>))}
            </tr>
          </thead>
          <tbody>
            {slice.map((r) => {
              const g = groupBy ? groupBy(r) : "";
              const header = groupBy && g !== lastGroup;
              lastGroup = g;
              return (
                <FragmentRow key={r.id} header={header ? g : null} span={shown.length + 2}>
                  <tr className="group border-b border-line last:border-0 hover:bg-page">
                    <td className="sticky left-0 z-10 w-[64px] min-w-[64px] bg-surface py-2 pl-3 pr-2 group-hover:bg-page">
                      <div className="flex items-center gap-2">
                        <input type="checkbox" aria-label={`Select ${r.symbol} for comparison`} checked={selected.includes(r.id)}
                          disabled={!selected.includes(r.id) && selected.length >= 8}
                          onChange={(e) => setSelected(e.target.checked ? [...selected, r.id] : selected.filter((x) => x !== r.id))} />
                        <button aria-label={r.watch ? `Remove ${r.symbol} from watchlist` : `Add ${r.symbol} to watchlist`} onClick={() => onWatch(r)}>
                          <Star size={14} className={r.watch ? "fill-[var(--warn)] text-[var(--warn)]" : "text-muted hover:text-ink"} />
                        </button>
                      </div>
                    </td>
                    <td className="sticky left-[64px] z-10 max-w-[260px] bg-surface px-2 py-2 group-hover:bg-page">
                      <Link href={`/instrument/${r.id}`} className="block">
                        <span className="font-semibold text-ink hover:underline">{r.symbol}</span>
                        <span className="block truncate text-xs text-muted">{r.name}</span>
                      </Link>
                    </td>
                    {shown.map((col) => (
                      <td key={col.id} className={`whitespace-nowrap px-2 py-2 ${col.align === "right" ? "text-right" : ""}`}>{col.cell(r)}</td>))}
                  </tr>
                </FragmentRow>);
            })}
          </tbody>
        </table>
      </div>
      {pages > 1 ? (
        <div className="flex items-center justify-end gap-2 border-t border-line px-3 py-2 text-xs text-ink2">
          <span className="tnum">{page * pageSize + 1}–{Math.min(sorted.length, (page + 1) * pageSize)} of {sorted.length}</span>
          <button className="btn px-2 py-1" disabled={page === 0} onClick={() => setPage(page - 1)} aria-label="Previous page"><ChevronLeft size={14} /></button>
          <button className="btn px-2 py-1" disabled={page >= pages - 1} onClick={() => setPage(page + 1)} aria-label="Next page"><ChevronRight size={14} /></button>
        </div>) : null}
    </>
  );
}

function FragmentRow({ header, span, children }: { header: string | null; span: number; children: React.ReactNode }) {
  return (
    <>
      {header ? (
        <tr className="border-b border-line bg-page"><td colSpan={span} className="px-3 py-1.5 text-xs font-semibold text-ink">{header}</td></tr>
      ) : null}
      {children}
    </>
  );
}
