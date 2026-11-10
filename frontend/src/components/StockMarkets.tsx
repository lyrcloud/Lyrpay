import { useEffect, useMemo, useRef, useState } from 'react';
import { api, type Quote, type StockGroup } from '../lib/api';
import { Change, Sparkline, ago, useLive, useNow } from './LiveMarkets';

const STOCKS_EVERY_MS = 60_000;

export const GROUP_LABELS: Record<StockGroup, string> = {
  ai: 'AI shares', index: 'Indices', etf: 'ETFs', stock: 'Stocks', commodity: 'Commodities',
};
const GROUP_BLURBS: Record<StockGroup, string> = {
  ai: 'Chip makers, cloud giants and software leading the AI boom',
  index: 'The world’s major stock market indices',
  etf: 'Popular index, sector, AI and bond funds',
  stock: 'Well-known companies across every sector',
  commodity: 'Gold, oil and other futures',
};

export function useStocks(group: StockGroup) {
  return useLive(() => api.stocks(group).then(r => r.quotes), STOCKS_EVERY_MS, [group]);
}

/** Indices are points, everything else is a price in its own currency. */
export function formatQuote(q: Quote, group?: StockGroup) {
  const digits = q.price >= 1000 ? 2 : q.price >= 1 ? 2 : 4;
  if (group === 'index' || q.symbol.startsWith('^')) {
    return new Intl.NumberFormat(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(q.price);
  }
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency: q.currency, maximumFractionDigits: digits }).format(q.price);
  } catch {
    return q.price.toFixed(digits);
  }
}

const ticker = (symbol: string) => symbol.replace(/^\^/, '').replace(/=F$/, '');
const compact = (n: number | null) => (n ? new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(n) : '–');

/** Which way each price moved since the last refresh, for a brief flash. */
function useQuoteMoves(quotes: Quote[] | null) {
  const last = useRef<Record<string, number>>({});
  return useMemo(() => {
    const moves: Record<string, 'up' | 'down'> = {};
    for (const q of quotes ?? []) {
      const before = last.current[q.symbol];
      if (before !== undefined && before !== q.price) moves[q.symbol] = q.price > before ? 'up' : 'down';
      last.current[q.symbol] = q.price;
    }
    return moves;
  }, [quotes]);
}

/** Cards follow the pointer with a slight 3D tilt. */
function tilt(event: React.PointerEvent<HTMLElement>) {
  if (event.pointerType !== 'mouse') return;
  const el = event.currentTarget;
  const r = el.getBoundingClientRect();
  const x = (event.clientX - r.left) / r.width - 0.5;
  const y = (event.clientY - r.top) / r.height - 0.5;
  el.style.setProperty('--rx', `${(-y * 6).toFixed(2)}deg`);
  el.style.setProperty('--ry', `${(x * 8).toFixed(2)}deg`);
  el.style.setProperty('--mx', `${((x + 0.5) * 100).toFixed(1)}%`);
  el.style.setProperty('--my', `${((y + 0.5) * 100).toFixed(1)}%`);
}
function untilt(event: React.PointerEvent<HTMLElement>) {
  event.currentTarget.style.removeProperty('--rx');
  event.currentTarget.style.removeProperty('--ry');
}

export type Filter = 'all' | 'gainers' | 'losers';

/** A grid of live quotes for one group, with search and gainers/losers sorting. */
export function StockBoard({ group, query, filter }: { group: StockGroup; query: string; filter: Filter }) {
  const live = useStocks(group);
  const moves = useQuoteMoves(live.value);
  const [open, setOpen] = useState<Quote | null>(null);
  const q = query.trim().toLowerCase();
  const quotes = useMemo(() => {
    let list = (live.value ?? []).filter(s => !q || s.name.toLowerCase().includes(q) || s.symbol.toLowerCase().includes(q));
    if (filter !== 'all') {
      list = list.filter(s => (filter === 'gainers' ? (s.change ?? 0) > 0 : (s.change ?? 0) < 0))
        .sort((a, b) => (filter === 'gainers' ? 1 : -1) * ((b.change ?? 0) - (a.change ?? 0)));
    }
    return list;
  }, [live.value, q, filter]);

  if (!live.value) {
    return live.failed
      ? <p className="empty">Share prices are unavailable right now. We’ll keep trying.</p>
      : <div className="coin-grid" aria-label="Loading">{Array.from({ length: 8 }, (_, i) => <div className="coin skeleton" key={i} />)}</div>;
  }
  return (
    <>
      <div className="coin-grid">
        {quotes.map((s, i) => (
          <button
            type="button" className="coin quote" key={s.symbol} style={{ animationDelay: `${Math.min(i, 20) * 25}ms` }}
            onPointerMove={tilt} onPointerLeave={untilt} onClick={() => setOpen(s)} title={`More about ${s.name}`}
          >
            <header>
              <span className={`avatar g-${group}`} aria-hidden="true">{ticker(s.symbol).slice(0, 4)}</span>
              <div><strong>{s.name}</strong><span>{ticker(s.symbol)} · {s.exchange}</span></div>
              <Change value={s.change} />
            </header>
            <p className={`coin-price ${moves[s.symbol] ? `flash-${moves[s.symbol]}` : ''}`} key={`${s.symbol}-${s.price}`}>{formatQuote(s, group)}</p>
            <Sparkline points={s.sparkline} positive={(s.sparkline.at(-1) ?? 0) >= (s.sparkline[0] ?? 0)} />
            <footer><span>Volume</span><span>{compact(s.volume)}</span></footer>
          </button>
        ))}
        {!quotes.length && <p className="empty">Nothing matches “{query}”.</p>}
      </div>
      {open && <QuoteDetail quote={live.value.find(s => s.symbol === open.symbol) ?? open} group={group} onClose={() => setOpen(null)} />}
    </>
  );
}

export function groupBlurb(group: StockGroup) {
  return GROUP_BLURBS[group];
}

/** A bigger chart with the day and 52-week ranges. */
export function QuoteDetail({ quote: s, group, onClose }: { quote: Quote; group?: StockGroup; onClose: () => void }) {
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    document.addEventListener('keydown', key);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', key);
      document.body.style.overflow = '';
    };
  }, [onClose]);
  const fmt = (n: number | null) => (n == null ? '–' : formatQuote({ ...s, price: n }, group));
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label={s.name} onClick={e => e.stopPropagation()}>
        <button className="sheet-close" onClick={onClose} aria-label="Close">×</button>
        <header className="sheet-head">
          <span className={`avatar big g-${group ?? 'stock'}`} aria-hidden="true">{ticker(s.symbol).slice(0, 4)}</span>
          <div>
            <h2>{s.name}</h2>
            <p className="muted">{ticker(s.symbol)} · {s.exchange} · {s.currency}</p>
          </div>
        </header>
        <div className="sheet-price">
          <strong>{formatQuote(s, group)}</strong>
          <Change value={s.change} />
          <span className="muted">today</span>
        </div>
        <div className="sheet-chart"><Sparkline points={s.sparkline} positive={(s.sparkline.at(-1) ?? 0) >= (s.sparkline[0] ?? 0)} /></div>
        <p className="hint">Last 5 trading days, hourly</p>
        <Range label="Day range" low={s.dayLow} high={s.dayHigh} value={s.price} fmt={fmt} />
        <Range label="52-week range" low={s.low52} high={s.high52} value={s.price} fmt={fmt} />
        <dl className="sheet-facts">
          <div><dt>Volume</dt><dd>{compact(s.volume)}</dd></div>
          <div><dt>Last trade</dt><dd>{s.time ? new Date(s.time * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '–'}</dd></div>
        </dl>
        <p className="hint">Prices can be delayed and are for information only, not investment advice.</p>
      </div>
    </div>
  );
}

function Range({ label, low, high, value, fmt }: { label: string; low: number | null; high: number | null; value: number; fmt: (n: number | null) => string }) {
  if (low == null || high == null || high <= low) return null;
  const at = Math.min(100, Math.max(0, ((value - low) / (high - low)) * 100));
  return (
    <div className="range">
      <div className="range-head"><span>{label}</span></div>
      <div className="range-bar"><span style={{ left: `${at}%` }} /></div>
      <div className="range-ends"><span>{fmt(low)}</span><span>{fmt(high)}</span></div>
    </div>
  );
}

/** The AI share market at a glance: breadth gauge, average move, leaders and a heatmap. */
export function AiPulse() {
  const live = useStocks('ai');
  const now = useNow();
  const [open, setOpen] = useState<Quote | null>(null);
  const quotes = live.value ?? [];
  const withChange = quotes.filter(q => q.change != null);
  const up = withChange.filter(q => (q.change ?? 0) > 0).length;
  const breadth = withChange.length ? up / withChange.length : 0.5;
  const avg = withChange.length ? withChange.reduce((sum, q) => sum + (q.change ?? 0), 0) / withChange.length : 0;
  const sorted = [...withChange].sort((a, b) => (b.change ?? 0) - (a.change ?? 0));
  const best = sorted[0];
  const worst = sorted.at(-1);
  const mood = avg > 1.5 ? 'Strong rally' : avg > 0.3 ? 'Leaning bullish' : avg < -1.5 ? 'Heavy selling' : avg < -0.3 ? 'Leaning bearish' : 'Mixed';
  // Half-circle gauge: 0% up points left, 100% up points right
  const angle = -90 + breadth * 180;

  return (
    <section className="ai-pulse reveal" id="ai">
      <div className="ai-pulse-head">
        <div>
          <span className="eyebrow"><span className={`live-dot${live.failed ? ' off' : ''}`} /> AI market pulse</span>
          <h2>Follow the <span className="gradient-text">AI share market</span> live</h2>
          <p className="muted">{GROUP_BLURBS.ai} · {live.failed && !live.value ? 'unavailable right now' : ago(live.at, now)}</p>
        </div>
      </div>

      <div className="ai-pulse-grid">
        <div className="pulse-card gauge-card">
          <svg viewBox="0 0 200 120" className="gauge" aria-hidden="true">
            <defs>
              <linearGradient id="gauge-grad" x1="0" x2="1">
                <stop offset="0%" stopColor="var(--negative)" />
                <stop offset="50%" stopColor="var(--c4)" />
                <stop offset="100%" stopColor="var(--positive)" />
              </linearGradient>
            </defs>
            <path d="M20 110 A80 80 0 0 1 180 110" fill="none" stroke="var(--surface-2)" strokeWidth="16" strokeLinecap="round" />
            <path d="M20 110 A80 80 0 0 1 180 110" fill="none" stroke="url(#gauge-grad)" strokeWidth="16" strokeLinecap="round" opacity="0.9" />
            <g className="gauge-needle" style={{ transform: `rotate(${angle}deg)` }}>
              <line x1="100" y1="110" x2="100" y2="42" stroke="var(--text)" strokeWidth="4" strokeLinecap="round" />
            </g>
            <circle cx="100" cy="110" r="8" fill="var(--text)" />
          </svg>
          <strong className="gauge-mood">{live.value ? mood : 'Loading…'}</strong>
          <span className="muted">{live.value ? `${up} of ${withChange.length} AI shares up today` : ' '}</span>
        </div>

        <div className="pulse-card pulse-stats">
          <div>
            <span className="muted">Average move</span>
            <strong className={avg >= 0 ? 'positive' : 'negative'}>{live.value ? `${avg >= 0 ? '+' : ''}${avg.toFixed(2)}%` : '–'}</strong>
          </div>
          <div>
            <span className="muted">Top gainer</span>
            {best ? <button className="leader" onClick={() => setOpen(best)}><b>{best.name}</b><Change value={best.change} /></button> : <strong>–</strong>}
          </div>
          <div>
            <span className="muted">Biggest drop</span>
            {worst ? <button className="leader" onClick={() => setOpen(worst)}><b>{worst.name}</b><Change value={worst.change} /></button> : <strong>–</strong>}
          </div>
        </div>

        <div className="pulse-card heatmap-card">
          <div className="heatmap">
            {(quotes.length ? quotes : Array.from({ length: 16 }, () => null)).map((q, i) => {
              if (!q) return <span className="heat skeleton" key={i} />;
              const c = Math.max(-5, Math.min(5, q.change ?? 0));
              const tone = c >= 0 ? 'var(--positive)' : 'var(--negative)';
              return (
                <button
                  key={q.symbol} className="heat" onClick={() => setOpen(q)} title={`${q.name}: ${formatQuote(q)}`}
                  style={{ background: `color-mix(in srgb, ${tone} ${18 + Math.abs(c) * 14}%, var(--surface))`, animationDelay: `${i * 30}ms` }}
                >
                  <b>{ticker(q.symbol)}</b>
                  <span>{q.change == null ? '–' : `${q.change >= 0 ? '+' : ''}${q.change.toFixed(2)}%`}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
      {open && <QuoteDetail quote={open} group="ai" onClose={() => setOpen(null)} />}
    </section>
  );
}

const EXCHANGES = [
  { name: 'New York', zone: 'America/New_York', open: 9.5, close: 16 },
  { name: 'London', zone: 'Europe/London', open: 8, close: 16.5 },
  { name: 'Frankfurt', zone: 'Europe/Berlin', open: 9, close: 17.5 },
  { name: 'Mumbai', zone: 'Asia/Kolkata', open: 9.25, close: 15.5 },
  { name: 'Hong Kong', zone: 'Asia/Hong_Kong', open: 9.5, close: 16 },
  { name: 'Tokyo', zone: 'Asia/Tokyo', open: 9, close: 15.5 },
];

/** Local time at the big exchanges and whether their regular session is on (holidays aside). */
export function MarketClocks() {
  const now = useNow(15_000);
  return (
    <div className="clocks reveal" aria-label="Stock exchange hours">
      {EXCHANGES.map(ex => {
        const parts = new Intl.DateTimeFormat('en-US', { timeZone: ex.zone, weekday: 'short', hour: 'numeric', minute: 'numeric', hourCycle: 'h23' })
          .formatToParts(now);
        const get = (t: string) => parts.find(p => p.type === t)?.value ?? '';
        const hours = Number(get('hour')) + Number(get('minute')) / 60;
        const weekday = !['Sat', 'Sun'].includes(get('weekday'));
        const open = weekday && hours >= ex.open && hours < ex.close;
        return (
          <div className={`clock${open ? ' open' : ''}`} key={ex.name}>
            <span className="clock-dot" />
            <div>
              <strong>{ex.name}</strong>
              <span>{get('hour').padStart(2, '0')}:{get('minute')} · {open ? 'Open' : 'Closed'}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
