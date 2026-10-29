import { useEffect, useMemo, useRef, useState } from 'react';
import { api, type AdminOverview, type Transaction } from '../lib/api';
import { countryName, flag, formatDate, formatMoney } from '../lib/format';
import { Businesses, Coupons, Settings } from './AdminRewards';

const REFRESH_MS = 60_000;

const KIND: Record<Transaction['kind'], { label: string; color: string }> = {
  topup: { label: 'Added', color: 'var(--c1)' },
  send: { label: 'Sent', color: 'var(--c2)' },
  receive: { label: 'Received', color: 'var(--c3)' },
  convert: { label: 'Converted', color: 'var(--c4)' },
  withdraw: { label: 'Withdrawn', color: 'var(--c5)' },
  giftcard: { label: 'Gift cards', color: 'var(--c6)' },
  redeem: { label: 'Redeemed', color: 'var(--c3)' },
  refund: { label: 'Refunded', color: 'var(--muted)' },
  bonus: { label: 'Bonuses', color: 'var(--c4)' },
};

// The second half of money already counted (a transfer's receive, a gift card's redeem or refund)
const SECOND_HALF: Transaction['kind'][] = ['receive', 'redeem', 'refund'];

const usd = (n: number, compact = false) =>
  new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', notation: compact ? 'compact' : 'standard', maximumFractionDigits: compact ? 1 : 2 }).format(n);

/** Animates a number from its last value to the new one. */
function useCountUp(target: number, ms = 900) {
  const [value, setValue] = useState(0);
  const from = useRef(0);
  useEffect(() => {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return setValue(target);
    const start = performance.now();
    const origin = from.current;
    let frame = 0;
    const step = (t: number) => {
      const p = Math.min(1, (t - start) / ms);
      const eased = 1 - (1 - p) ** 3;
      setValue(origin + (target - origin) * eased);
      if (p < 1) frame = requestAnimationFrame(step);
      else from.current = target;
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [target, ms]);
  return value;
}

function Kpi({ label, value, format, note, tone }: { label: string; value: number; format: (n: number) => string; note?: string; tone?: string }) {
  const shown = useCountUp(value);
  return (
    <div className={`kpi ${tone ?? ''}`}>
      <span>{label}</span>
      <strong>{format(shown)}</strong>
      {note && <em>{note}</em>}
    </div>
  );
}

function VolumeChart({ daily }: { daily: AdminOverview['daily'] }) {
  const [hover, setHover] = useState<number | null>(null);
  const max = Math.max(1, ...daily.map(d => d.volumeUsd));
  const maxSignups = Math.max(1, ...daily.map(d => d.signups));
  const w = 100 / daily.length;
  const line = daily.map((d, i) => `${i ? 'L' : 'M'}${(i + 0.5) * w},${96 - (d.signups / maxSignups) * 80}`).join('');
  const active = hover == null ? null : daily[hover];
  return (
    <div className="chart" onMouseLeave={() => setHover(null)}>
      <div className="chart-legend">
        <span><i style={{ background: 'var(--accent)' }} /> Volume (USD)</span>
        <span><i className="line" /> Sign-ups</span>
        {active && (
          <span className="chart-readout">
            {new Date(active.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} · {usd(active.volumeUsd)} · {active.count} payments · {active.signups} sign-ups
          </span>
        )}
      </div>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Daily volume and sign-ups">
        {[25, 50, 75].map(y => <line key={y} x1="0" x2="100" y1={y} y2={y} className="grid-line" vectorEffect="non-scaling-stroke" />)}
        {daily.map((d, i) => {
          const h = (d.volumeUsd / max) * 90;
          return (
            <g key={d.date} onMouseEnter={() => setHover(i)}>
              <rect x={i * w} width={w} y="0" height="100" fill="transparent" />
              <rect
                className={`bar${hover === i ? ' active' : ''}`} x={i * w + w * 0.18} width={w * 0.64}
                y={100 - Math.max(h, d.volumeUsd ? 1.5 : 0.6)} height={Math.max(h, d.volumeUsd ? 1.5 : 0.6)} rx="0.8"
                style={{ animationDelay: `${i * 30}ms` }}
              />
            </g>
          );
        })}
        <path d={line} className="signup-line" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="chart-axis">
        {daily.map((d, i) => (
          <span key={d.date} className={i % Math.ceil(daily.length / 7) ? 'hide' : ''}>
            {new Date(d.date).toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' })}
          </span>
        ))}
      </div>
    </div>
  );
}

function KindBreakdown({ byKind }: { byKind: AdminOverview['byKind'] }) {
  const rows = (Object.keys(KIND) as Transaction['kind'][]).filter(k => !SECOND_HALF.includes(k)).map(k => ({ kind: k, ...(byKind[k] ?? { count: 0, volumeUsd: 0 }) }));
  const max = Math.max(1, ...rows.map(r => r.volumeUsd));
  return (
    <ul className="hbars">
      {rows.map(r => (
        <li key={r.kind}>
          <div><span>{KIND[r.kind].label}</span><span className="muted">{r.count} · {usd(r.volumeUsd)}</span></div>
          <div className="hbar"><i style={{ width: `${(r.volumeUsd / max) * 100}%`, background: KIND[r.kind].color }} /></div>
        </li>
      ))}
    </ul>
  );
}

type SortKey = 'created' | 'name' | 'heldUsd' | 'country';

type Section = 'overview' | 'coupons' | 'businesses' | 'settings';

export default function Admin() {
  const [section, setSection] = useState<Section>('overview');
  const [waiting, setWaiting] = useState(0);
  return (
    <div className="admin">
      <nav className="admin-nav" aria-label="Admin sections">
        {(['overview', 'coupons', 'businesses', 'settings'] as Section[]).map(s => (
          <button key={s} aria-current={section === s} onClick={() => setSection(s)}>
            {{ overview: 'Overview', coupons: 'Promo codes', businesses: 'Businesses', settings: 'Rewards settings' }[s]}
            {s === 'businesses' && waiting > 0 && <span className="count">{waiting}</span>}
          </button>
        ))}
      </nav>
      {section === 'overview' && <Overview onWaiting={setWaiting} onOpen={setSection} />}
      {section === 'coupons' && <Coupons />}
      {section === 'businesses' && <Businesses />}
      {section === 'settings' && <Settings />}
    </div>
  );
}

function Overview({ onWaiting, onOpen }: { onWaiting: (n: number) => void; onOpen: (s: Section) => void }) {
  const [data, setData] = useState<AdminOverview | null>(null);
  const [days, setDays] = useState(14);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: 'created', desc: true });

  const load = () => {
    setLoading(true);
    api.adminOverview(days)
      .then(d => { setData(d); setError(''); onWaiting(d.totals.businessApplications); })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    const timer = setInterval(() => !document.hidden && load(), REFRESH_MS);
    return () => clearInterval(timer);
  }, [days]);

  const users = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = (data?.users ?? []).filter(u => !q || [u.name, u.email, u.phone, u.country, countryName(u.country || 'ZZ')].some(v => v.toLowerCase().includes(q)));
    const dir = sort.desc ? -1 : 1;
    return [...list].sort((a, b) => {
      const x = a[sort.key], y = b[sort.key];
      return dir * (typeof x === 'number' ? x - (y as number) : String(x).localeCompare(String(y)));
    });
  }, [data, query, sort]);

  const sortBy = (key: SortKey) => setSort(s => ({ key, desc: s.key === key ? !s.desc : key !== 'name' }));
  const th = (key: SortKey, label: string, className = '') => (
    <th className={`sortable ${className}`} aria-sort={sort.key === key ? (sort.desc ? 'descending' : 'ascending') : 'none'} onClick={() => sortBy(key)}>
      {label}{sort.key === key ? (sort.desc ? ' ↓' : ' ↑') : ''}
    </th>
  );

  if (error && !data) return <div className="card"><p className="error">{error}</p></div>;
  if (!data) return <div className="admin-section"><div className="kpis">{Array.from({ length: 6 }, (_, i) => <div className="kpi skeleton" key={i} />)}</div></div>;

  const t = data.totals;
  const maxHeld = Math.max(1, ...data.held.map(h => h.usd));

  return (
    <div className="admin-section">
      <div className="admin-head">
        <div>
          <span className="badge admin-badge">★ Super admin</span>
          <h1>Lyrpay overview</h1>
          <p className="muted">Everyone's accounts and activity, valued in US dollars at today's rates · updated {formatDate(data.updated)}</p>
        </div>
        <div className="admin-actions">
          <div className="chips">
            {[7, 14, 30, 90].map(d => <button key={d} aria-pressed={days === d} onClick={() => setDays(d)}>{d}d</button>)}
          </div>
          <button className={`button refresh${loading ? ' spinning' : ''}`} onClick={load} disabled={loading} aria-label="Refresh">↻</button>
        </div>
      </div>
      {error && <p className="error">{error}</p>}

      <div className="kpis">
        <Kpi label="Accounts" value={t.users} format={n => Math.round(n).toLocaleString()} note={`+${t.newUsers} in ${days} days`} tone="k1" />
        <Kpi label="Money held" value={t.heldUsd} format={n => usd(n)} note={`${data.held.length} currencies`} tone="k2" />
        <Kpi label="All-time volume" value={t.volumeUsd} format={n => usd(n, n >= 100000)} tone="k3" />
        <Kpi label="Transactions" value={t.transactions} format={n => Math.round(n).toLocaleString()} tone="k4" />
        <Kpi label="Pending withdrawals" value={t.pendingWithdrawals} format={n => Math.round(n).toString()} note={t.pendingWithdrawals ? 'settled every 15 min' : 'all clear'} tone={t.pendingWithdrawals ? 'warn' : 'k5'} />
        <Kpi label="Unsubscribed" value={t.unsubscribed} format={n => Math.round(n).toString()} note="from emails" tone="k6" />
        <Kpi label="Gift cards unredeemed" value={t.giftCardsOutstandingUsd} format={n => usd(n)} note={`${t.giftCardsSold} sold`} tone="k3" />
        <Kpi label="Referred sign-ups" value={t.referrals} format={n => Math.round(n).toString()} note="through invite links" tone="k1" />
        <button className="kpi kpi-button k2" onClick={() => onOpen('businesses')}>
          <span>Business accounts</span>
          <strong>{t.businesses}</strong>
          <em className={t.businessApplications ? 'warn-text' : ''}>{t.businessApplications ? `${t.businessApplications} waiting for review →` : 'none waiting'}</em>
        </button>
        <button className="kpi kpi-button k4" onClick={() => onOpen('coupons')}>
          <span>Active promo codes</span>
          <strong>{t.activeCoupons}</strong>
          <em>manage →</em>
        </button>
      </div>

      <div className="admin-grid">
        <section className="card span-2">
          <h2>Daily volume · last {days} days</h2>
          <VolumeChart daily={data.daily} />
        </section>

        <section className="card">
          <h2>Volume by type</h2>
          <KindBreakdown byKind={data.byKind} />
        </section>

        <section className="card">
          <h2>Money held by currency</h2>
          {data.held.length ? (
            <ul className="hbars">
              {data.held.map(h => (
                <li key={h.currency}>
                  <div><span><strong>{h.currency}</strong> {formatMoney(h.amount, h.currency)}</span><span className="muted">{usd(h.usd)}</span></div>
                  <div className="hbar"><i style={{ width: `${(h.usd / maxHeld) * 100}%` }} /></div>
                </li>
              ))}
            </ul>
          ) : <p className="empty">Nobody holds a balance yet.</p>}
        </section>

        <section className="card span-2">
          <div className="section-head">
            <h2>Accounts <span className="muted">({users.length})</span></h2>
            <input type="search" placeholder="Search name, email, phone or country" value={query} onChange={e => setQuery(e.target.value)} />
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  {th('name', 'Person')}
                  {th('country', 'Country', 'hide-sm')}
                  {th('created', 'Joined', 'hide-sm')}
                  <th className="hide-sm">Payouts</th>
                  {th('heldUsd', 'Holds', 'num')}
                </tr>
              </thead>
              <tbody>
                {users.map(u => (
                  <tr key={`${u.email}${u.phone}${u.created}`}>
                    <td>
                      <div className="person">
                        <span className="avatar">{(u.name || u.email || u.phone || '?')[0].toUpperCase()}</span>
                        <div>
                          <strong>{u.name || '—'}{u.business === 'approved' && <span className="tag">Business</span>}{u.referred && <span className="tag soft">Referred</span>}</strong>
                          <span>{u.email || u.phone}</span>
                        </div>
                      </div>
                    </td>
                    <td className="hide-sm">{u.country ? `${flag(u.country)} ${countryName(u.country)}` : '—'}</td>
                    <td className="hide-sm muted">{u.created ? formatDate(u.created) : '—'}</td>
                    <td className="hide-sm"><span className={`badge ${u.payouts ? 'live' : 'soon'}`}>{u.payouts ? 'Set up' : 'None'}</span></td>
                    <td className="num"><strong>{usd(u.heldUsd)}</strong></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!users.length && <p className="empty">No accounts match “{query}”.</p>}
          </div>
        </section>

        <section className="card span-2">
          <h2>Latest activity</h2>
          {data.recent.length ? (
            <ul className="feed">
              {data.recent.map(tx => (
                <li key={tx.id}>
                  <i className="feed-dot" style={{ background: KIND[tx.kind].color }} />
                  <div>
                    <strong>{tx.user || 'Someone'}</strong> <span className="muted">{KIND[tx.kind].label.toLowerCase()}{tx.counterparty && ['send', 'receive'].includes(tx.kind) ? ` · ${tx.counterparty}` : ''}</span>
                    <span className="feed-time">{formatDate(tx.at)}{tx.status && tx.status !== 'paid' ? ` · ${tx.status}` : ''}</span>
                  </div>
                  <strong className={tx.amount.startsWith('-') ? '' : 'positive'}>{tx.amount.startsWith('-') ? '' : '+'}{formatMoney(tx.amount, tx.currency)}</strong>
                </li>
              ))}
            </ul>
          ) : <p className="empty">No payments yet.</p>}
        </section>
      </div>
    </div>
  );
}
