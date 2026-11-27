import { useEffect, useMemo, useState } from 'react';
import { api, type AdminLine, type AdminLines } from '../lib/api';
import { formatDate, formatMoney } from '../lib/format';
import AdminAccount from './AdminAccount';

const KINDS: [string, string][] = [
  ['', 'All'], ['topup', 'Added'], ['send', 'Sent'], ['receive', 'Received'], ['convert', 'Converted'], ['withdraw', 'Withdrawals'],
  ['payment', 'App payments'], ['card', 'Card'], ['giftcard', 'Gift cards'], ['refund', 'Refunds'], ['bonus', 'Bonuses'], ['adjust', 'Adjustments'],
];
const ICON: Record<string, string> = {
  topup: '＋', payment: '🛍', send: '↗', receive: '↙', convert: '⇄', withdraw: '🏦', giftcard: '🎁', redeem: '🎟', refund: '↩', bonus: '⭐', card: '💳', adjust: '✎',
};
const STATUS: Record<AdminLine['status'], string> = { done: 'Done', pending: 'Pending', failed: 'Failed' };

function csv(rows: AdminLine[]) {
  const head = ['tracking', 'at', 'app', 'name', 'email', 'kind', 'status', 'amount', 'currency', 'usd', 'counterparty', 'note', 'provider', 'reference'];
  const cell = (v: string) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const body = rows.map(r => [r.tracking, r.at, r.app, r.name, r.email, r.kind, r.status, r.amount, r.currency, r.usd, r.counterparty, r.note, r.rail, r.reference].map(cell).join(','));
  const url = URL.createObjectURL(new Blob([[head.join(','), ...body].join('\n')], { type: 'text/csv' }));
  const a = Object.assign(document.createElement('a'), { href: url, download: `lyrpay-transactions-${new Date().toISOString().slice(0, 10)}.csv` });
  a.click();
  URL.revokeObjectURL(url);
}

/** Every transaction across Lyrpay and the Lyr apps. Each row opens its account, where it can be edited or deleted. */
export default function AdminTransactions() {
  const [q, setQ] = useState('');
  const [kind, setKind] = useState('');
  const [status, setStatus] = useState('');
  const [app, setApp] = useState('');
  const [days, setDays] = useState('30');
  const [live, setLive] = useState(true);
  const [data, setData] = useState<AdminLines | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  // Typing settles for a moment before searching
  useEffect(() => { const t = setTimeout(() => setQuery(q), 350); return () => clearTimeout(t); }, [q]);
  const filters = useMemo(() => ({ q: query, kind, status, app, days }), [query, kind, status, app, days]);
  const load = () => {
    setBusy(true);
    return api.adminTransactions(filters).then(d => { setData(d); setError(''); }).catch(e => setError((e as Error).message)).finally(() => setBusy(false));
  };
  useEffect(() => { load(); }, [filters]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => document.visibilityState === 'visible' && !open && load(), 15000);
    return () => clearInterval(timer);
  }, [live, filters, open]); // eslint-disable-line react-hooks/exhaustive-deps

  const totals = data?.totals;
  return (
    <div className="admin-section">
      <div className="kpis">
        <div className="kpi k1"><span>Transactions</span><strong>{totals?.count ?? '…'}</strong><em>last {days} days, as filtered</em></div>
        <div className="kpi k5"><span>Money in</span><strong>{totals ? formatMoney(totals.inUsd, 'USD') : '…'}</strong><em>in US dollars</em></div>
        <div className="kpi k6"><span>Money out</span><strong>{totals ? formatMoney(totals.outUsd, 'USD') : '…'}</strong><em>in US dollars</em></div>
        <button className={`kpi kpi-button ${totals?.pending ? 'warn' : 'k3'}`} onClick={() => setStatus(status === 'pending' ? '' : 'pending')}>
          <span>Pending</span><strong>{totals?.pending ?? '…'}</strong><em>{status === 'pending' ? 'showing only these' : 'tap to show'}</em>
        </button>
        <button className="kpi kpi-button k4" onClick={() => setStatus(status === 'failed' ? '' : 'failed')}>
          <span>Failed</span><strong>{totals?.failed ?? '…'}</strong><em>{status === 'failed' ? 'showing only these' : 'tap to show'}</em>
        </button>
      </div>

      <section className="card">
        <div className="section-head">
          <h2>All transactions</h2>
          <div className="admin-actions">
            <label className="inline-label"><span className={`live-dot${live ? '' : ' off'}`} />Live
              <span className="switch"><input type="checkbox" checked={live} onChange={e => setLive(e.target.checked)} /><span /></span>
            </label>
            <button className={`button refresh${busy ? ' spinning' : ''}`} onClick={load} title="Refresh" aria-label="Refresh">↻</button>
            <button className="button" disabled={!data?.transactions.length} onClick={() => data && csv(data.transactions)}>⬇ CSV</button>
          </div>
        </div>
        <div className="panel-tools">
          <input type="search" value={q} onChange={e => setQ(e.target.value)} placeholder="Search name, email, tracking number, note…" />
          <select value={app} onChange={e => setApp(e.target.value)} aria-label="App">
            <option value="">All apps</option>
            <option value="lyrpay">Lyrpay</option>
            {data?.apps.map(a => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
          </select>
          <select value={status} onChange={e => setStatus(e.target.value)} aria-label="Status">
            <option value="">Any status</option><option value="done">Done</option><option value="pending">Pending</option><option value="failed">Failed</option>
          </select>
          <select value={days} onChange={e => setDays(e.target.value)} aria-label="Period">
            <option value="1">Today</option><option value="7">7 days</option><option value="30">30 days</option><option value="90">90 days</option><option value="365">1 year</option><option value="3650">All time</option>
          </select>
        </div>
        <div className="chips kind-chips">
          {KINDS.map(([k, label]) => <button key={k || 'all'} aria-pressed={kind === k} onClick={() => setKind(k)}>{ICON[k] ? `${ICON[k]} ` : ''}{label}</button>)}
        </div>
        {error && <p className="error">{error}</p>}
        {!data && !error && <div className="skeleton" style={{ height: 240 }} />}
        {data && !data.transactions.length && <p className="empty">No transactions match.</p>}
        {data && data.transactions.length > 0 && (
          <div className="table-wrap">
            <table className="table all-lines">
              <thead><tr><th>When</th><th>Who</th><th>What</th><th>App</th><th>Status</th><th className="num">Amount</th><th>Tracking</th></tr></thead>
              <tbody>
                {data.transactions.map(t => (
                  <tr key={t.sub + t.sk} onClick={() => setOpen(t.sub)} className={`clickable status-${t.status}`} title="Open this account to edit or delete">
                    <td className="nowrap">{formatDate(t.at)}</td>
                    <td><div className="person"><span className="avatar">{(t.name || t.email || '?')[0]?.toUpperCase()}</span>
                      <div><strong>{t.name || '—'}{t.isApp && <span className="tag">app</span>}</strong><span>{t.email || t.sub}</span></div></div></td>
                    <td><span className={`tx-icon small kind-${t.kind}`}>{ICON[t.kind] ?? '•'}</span> {t.kind}{t.counterparty ? ` · ${t.counterparty}` : ''}{t.note ? <span className="muted small"> · {t.note}</span> : null}</td>
                    <td><span className={`badge app-badge app-${t.app}`}>{t.app === 'lyrpay' ? 'Lyrpay' : data.apps.find(a => a.id === t.app)?.name || t.app}</span></td>
                    <td><span className={`badge ${t.status === 'done' ? 'live' : t.status === 'pending' ? 'warn' : 'soon'}`}>{STATUS[t.status]}</span></td>
                    <td className={`num ${t.amount.startsWith('-') ? '' : 'positive'}`}>{t.amount.startsWith('-') ? '' : '+'}{formatMoney(t.amount, t.currency)}</td>
                    <td className="mono small nowrap">{t.tracking}{t.reference && <div className="muted small">{t.rail || 'ref'}: {t.reference}</div>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && data.totals.count > data.transactions.length && <p className="hint">Showing the newest {data.transactions.length} of {data.totals.count}. Narrow the filters to see others.</p>}
      </section>
      {open && <AdminAccount sub={open} onClose={() => setOpen(null)} onChanged={load} />}
    </div>
  );
}
