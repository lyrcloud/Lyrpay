import { useEffect, useState } from 'react';
import { api, type BusinessApplication, type Coupon, type RewardSettings } from '../lib/api';
import { countryName, flag, formatDate, formatMoney } from '../lib/format';

function useLoad<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const reload = () => load().then(d => { setData(d); setError(''); }).catch(err => setError(err.message));
  useEffect(() => { reload(); }, []);
  return { data, error, reload, setError };
}

const EMPTY_COUPON = { code: '', kind: 'percent' as Coupon['kind'], value: '10', maxBonusUsd: '20', minTopupUsd: '10', maxUses: '100', oncePerPerson: true, expires: '', description: '' };

function couponSummary(c: Coupon) {
  const bonus = c.kind === 'percent' ? `${Number(c.value)}% bonus, up to ${formatMoney(c.maxBonusUsd, 'USD')}` : `${formatMoney(c.value, 'USD')} bonus`;
  return `${bonus} on top-ups of ${formatMoney(c.minTopupUsd, 'USD')}+`;
}

export function Coupons() {
  const { data, error, reload, setError } = useLoad(api.adminCoupons);
  const [form, setForm] = useState(EMPTY_COUPON);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof EMPTY_COUPON) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm(f => ({ ...f, [key]: e.target.type === 'checkbox' ? (e.target as HTMLInputElement).checked : e.target.value }));

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      await api.createCoupon({ ...form, maxUses: Number(form.maxUses) || 0 } as unknown as Partial<Coupon>);
      setForm(EMPTY_COUPON);
      reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (c: Coupon) => {
    try {
      await api.toggleCoupon(c.code, !c.active);
      reload();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <div className="admin-grid">
      <section className="card">
        <h2>New promo code</h2>
        <form className="form" onSubmit={create}>
          <div className="two-col">
            <label>Code<input required value={form.code} onChange={e => setForm(f => ({ ...f, code: e.target.value.toUpperCase().replace(/[^A-Z0-9_-]/g, '') }))} placeholder="WELCOME10" maxLength={24} /></label>
            <label>Bonus type
              <select value={form.kind} onChange={set('kind')}>
                <option value="percent">% of the top-up</option>
                <option value="fixed">Fixed amount (USD)</option>
              </select>
            </label>
          </div>
          <div className="two-col">
            <label>{form.kind === 'percent' ? 'Bonus %' : 'Bonus (USD)'}<input required inputMode="decimal" value={form.value} onChange={set('value')} /></label>
            {form.kind === 'percent' && <label>Largest bonus (USD)<input inputMode="decimal" value={form.maxBonusUsd} onChange={set('maxBonusUsd')} /></label>}
          </div>
          <div className="two-col">
            <label>Smallest top-up (USD)<input inputMode="decimal" value={form.minTopupUsd} onChange={set('minTopupUsd')} /></label>
            <label>Total uses (0 = no limit)<input inputMode="numeric" value={form.maxUses} onChange={set('maxUses')} /></label>
          </div>
          <div className="two-col">
            <label>Expires<input type="date" value={form.expires} onChange={set('expires')} /></label>
            <label className="check"><input type="checkbox" checked={form.oncePerPerson} onChange={set('oncePerPerson')} /> Once per person</label>
          </div>
          <label>Description<input value={form.description} onChange={set('description')} placeholder="Shown to people when they use it" maxLength={120} /></label>
          {error && <p className="error">{error}</p>}
          <button className="button primary" disabled={busy}>{busy ? 'Creating…' : 'Create promo code'}</button>
          <p className="hint">Bonuses are real money added to people's balances, paid by Lyrpay. Keep limits on every code.</p>
        </form>
      </section>

      <section className="card">
        <h2>Promo codes <span className="muted">({data?.coupons.length ?? 0})</span></h2>
        {!data ? <p className="empty">Loading…</p> : !data.coupons.length ? <p className="empty">No promo codes yet.</p> : (
          <ul className="coupon-list">
            {data.coupons.map(c => {
              const expired = c.expires && c.expires < new Date().toISOString().slice(0, 10);
              const used = c.maxUses ? Math.min(100, (c.uses / c.maxUses) * 100) : 0;
              return (
                <li key={c.code} className={c.active && !expired ? '' : 'off'}>
                  <div className="coupon-head">
                    <code className="coupon-code">{c.code}</code>
                    <span className={`badge ${c.active && !expired ? 'live' : 'soon'}`}>{expired ? 'Expired' : c.active ? 'Active' : 'Paused'}</span>
                    <label className="switch" title={c.active ? 'Pause' : 'Turn on'}>
                      <input type="checkbox" checked={c.active} onChange={() => toggle(c)} aria-label={`${c.code} active`} />
                      <span />
                    </label>
                  </div>
                  <p>{couponSummary(c)}{c.oncePerPerson ? ' · once per person' : ''}{c.expires ? ` · until ${c.expires}` : ''}</p>
                  {c.description && <p className="muted">{c.description}</p>}
                  <div className="hbar"><i style={{ width: `${c.maxUses ? used : 4}%` }} /></div>
                  <span className="muted small">{c.uses} used{c.maxUses ? ` of ${c.maxUses}` : ''}</span>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

export function Businesses() {
  const { data, error, reload, setError } = useLoad(api.adminBusinesses);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState('');
  const [filter, setFilter] = useState<'pending' | 'approved' | 'rejected' | 'all'>('pending');

  const review = async (b: BusinessApplication, status: 'approved' | 'rejected') => {
    if (status === 'rejected' && !notes[b.sub]?.trim() && !confirm(`Reject ${b.company} without a note?`)) return;
    setBusy(b.sub);
    try {
      await api.reviewBusiness(b.sub, status, notes[b.sub] ?? '');
      reload();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy('');
    }
  };

  const list = (data?.businesses ?? []).filter(b => filter === 'all' || b.status === filter);
  const count = (s: string) => (data?.businesses ?? []).filter(b => b.status === s).length;

  return (
    <section className="card">
      <div className="section-head">
        <h2>Business accounts</h2>
        <div className="chips">
          {(['pending', 'approved', 'rejected', 'all'] as const).map(f => (
            <button key={f} aria-pressed={filter === f} onClick={() => setFilter(f)}>
              {{ pending: 'Waiting', approved: 'Approved', rejected: 'Rejected', all: 'All' }[f]}{f !== 'all' ? ` ${count(f)}` : ''}
            </button>
          ))}
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {!data ? <p className="empty">Loading…</p> : !list.length ? <p className="empty">Nothing here.</p> : (
        <ul className="applications">
          {list.map(b => (
            <li key={b.sub}>
              <div className="application-head">
                <span className="avatar">{b.company[0]?.toUpperCase()}</span>
                <div>
                  <strong>{b.company}</strong>
                  <span>{b.name} · {b.email || b.phone}</span>
                </div>
                <span className={`badge ${b.status === 'approved' ? 'live' : b.status === 'pending' ? 'warn' : 'soon'}`}>{b.status}</span>
              </div>
              <dl className="facts">
                <div><dt>Country</dt><dd>{flag(b.country)} {countryName(b.country)}</dd></div>
                <div><dt>Registration</dt><dd>{b.registrationId}</dd></div>
                <div><dt>Volume</dt><dd>{b.monthlyVolume}</dd></div>
                <div><dt>Website</dt><dd>{b.website ? <a href={b.website.startsWith('http') ? b.website : `https://${b.website}`} target="_blank" rel="noreferrer noopener">{b.website}</a> : '—'}</dd></div>
                <div><dt>Applied</dt><dd>{formatDate(b.applied)}</dd></div>
              </dl>
              {b.about && <p className="muted">“{b.about}”</p>}
              {b.status === 'pending' ? (
                <div className="review">
                  <input placeholder="Note to the applicant (sent with a rejection)" value={notes[b.sub] ?? ''} onChange={e => setNotes(n => ({ ...n, [b.sub]: e.target.value }))} />
                  <button className="button primary" disabled={busy === b.sub} onClick={() => review(b, 'approved')}>Approve</button>
                  <button className="button" disabled={busy === b.sub} onClick={() => review(b, 'rejected')}>Reject</button>
                </div>
              ) : (
                <p className="muted small">{b.status === 'approved' ? 'Approved' : 'Rejected'} {b.reviewed ? formatDate(b.reviewed) : ''}{b.note ? ` · “${b.note}”` : ''}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function Settings() {
  const { data, error, setError } = useLoad(api.adminSettings);
  const [form, setForm] = useState<Partial<Record<keyof RewardSettings, string>>>({});
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (data) setForm(Object.fromEntries(Object.entries(data.settings).map(([k, v]) => [k, String(v)])));
  }, [data]);

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setSaved(false);
    try {
      await api.saveSettings(form);
      setSaved(true);
      setError('');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const set = (key: keyof RewardSettings) => (e: React.ChangeEvent<HTMLInputElement>) => setForm(f => ({ ...f, [key]: e.target.value }));

  if (!data) return <section className="card">{error ? <p className="error">{error}</p> : <p className="empty">Loading…</p>}</section>;
  return (
    <section className="card settings-card">
      <h2>Referrals and business pricing</h2>
      <form className="form" onSubmit={save}>
        <div className="two-col">
          <label>Bonus to the person who refers (USD)<input inputMode="decimal" value={form.referrerBonusUsd ?? ''} onChange={set('referrerBonusUsd')} /></label>
          <label>Welcome bonus to the friend (USD)<input inputMode="decimal" value={form.friendBonusUsd ?? ''} onChange={set('friendBonusUsd')} /></label>
        </div>
        <div className="two-col">
          <label>Friend's first top-up must be at least (USD)<input inputMode="decimal" value={form.referralMinTopupUsd ?? ''} onChange={set('referralMinTopupUsd')} /></label>
          <label>Business conversion fee (basis points, 100 = 1%)<input inputMode="numeric" value={form.businessFeeBps ?? ''} onChange={set('businessFeeBps')} /></label>
        </div>
        <p className="hint">Everyone else converts at {data.standardFeeBps / 100}%. Bonuses are paid once per referred person, after their first qualifying top-up. Set a bonus to 0 to stop paying it.</p>
        {error && <p className="error">{error}</p>}
        {saved && <p className="info">Saved. New referrals and conversions use these settings.</p>}
        <button className="button primary" disabled={busy}>{busy ? 'Saving…' : 'Save settings'}</button>
      </form>
    </section>
  );
}
