import { useEffect, useState } from 'react';
import { api, type Merchant, type MerchantPayment } from '../lib/api';
import { formatDate, formatMoney } from '../lib/format';
import { useCopy } from './Rewards';

const SUGGESTED: Record<string, { name: string; returnOrigins: string }> = {
  lyrtrip: { name: 'Lyrtrip', returnOrigins: 'https://www.lyrtrip.com https://lyrtrip.com' },
  lyrshop: { name: 'Lyrshop', returnOrigins: 'https://lyrshop.com https://www.lyrshop.com' },
  lyrsoft: { name: 'Lyrsoft', returnOrigins: 'https://lyrsoft.com https://www.lyrsoft.com' },
  lyrcloud: { name: 'Lyrcloud', returnOrigins: 'https://lyrcloud.com https://www.lyrcloud.com' },
};
const EMPTY = { id: '', name: '', webhookUrl: '', returnOrigins: '' };

/** Admin: the Lyr apps that take "Pay with Lyrpay" payments, their keys, balances and latest payments. */
export default function AdminApps() {
  const [data, setData] = useState<{ merchants: Merchant[]; payments: MerchantPayment[] } | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [keys, setKeys] = useState<{ id: string; secretKey: string; webhookSecret: string } | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const { copied, copy } = useCopy();
  const load = () => api.adminMerchants().then(setData).catch(err => setError(err.message));
  useEffect(() => { load(); }, []);

  const save = async (body: Record<string, unknown>) => {
    setBusy(true);
    setError('');
    try {
      const saved = await api.saveMerchant(body);
      if (saved.secretKey) setKeys({ id: saved.id, secretKey: saved.secretKey, webhookSecret: saved.webhookSecret ?? '' });
      setForm(EMPTY);
      load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const set = (key: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value;
    setForm(f => ({ ...f, [key]: value, ...(key === 'id' && SUGGESTED[value.trim().toLowerCase()] && !f.name ? SUGGESTED[value.trim().toLowerCase()] : {}) }));
  };
  const edit = (m: Merchant) => setForm({ id: m.id, name: m.name, webhookUrl: m.webhookUrl, returnOrigins: m.returnOrigins.join(' ') });

  return (
    <div className="grid">
      <section className="card">
        <h2>Add or change a Lyr app</h2>
        <form className="form" onSubmit={e => { e.preventDefault(); save(form); }}>
          <div className="two-col">
            <label>App id<input required value={form.id} onChange={set('id')} placeholder="lyrtrip" autoComplete="off" /></label>
            <label>Name customers see<input required value={form.name} onChange={set('name')} placeholder="Lyrtrip" /></label>
          </div>
          <label>Sites customers return to<input required value={form.returnOrigins} onChange={set('returnOrigins')} placeholder="https://www.lyrtrip.com https://lyrtrip.com" /></label>
          <label>Callback address (the app's endpoint for payment.paid / payment.refunded)<input value={form.webhookUrl} onChange={set('webhookUrl')} placeholder="https://…/lyrpay/callback" /></label>
          {error && <p className="error">{error}</p>}
          <button className="button primary" disabled={busy}>{busy ? 'Saving…' : 'Save app'}</button>
          <p className="hint">A new app gets its secret key and callback secret once, here. Put them in that app's settings; Lyrpay keeps only a fingerprint of the key.</p>
        </form>
        {keys && (
          <div className="keys-once">
            <h3>Keys for {keys.id}: copy them now</h3>
            {[['Secret key (LYRPAY_SECRET_KEY)', keys.secretKey], ['Callback secret (LYRPAY_WEBHOOK_SECRET)', keys.webhookSecret]].map(([label, value]) => (
              <label key={label}>{label}
                <div className="copy-row">
                  <input readOnly value={value} onFocus={e => e.target.select()} />
                  <button type="button" className="button" onClick={() => copy(value)}>{copied === value ? 'Copied ✓' : 'Copy'}</button>
                </div>
              </label>
            ))}
            <button className="link" onClick={() => setKeys(null)}>I've saved them</button>
          </div>
        )}
      </section>
      <section className="card">
        <h2>Lyr apps</h2>
        {!data ? <p className="empty">Loading…</p> : data.merchants.length === 0 ? <p className="empty">No apps yet. Add Lyrtrip first.</p> : (
          <ul className="coupon-list">
            {data.merchants.map(m => (
              <li key={m.id}>
                <div className="section-head">
                  <strong>{m.name} <span className="muted small">{m.id} · key …{m.keyHint}</span></strong>
                  <span className={`badge ${m.active ? '' : 'warn'}`}>{m.active ? 'On' : 'Off'}</span>
                </div>
                <p className="muted small">
                  {Object.keys(m.balances).length ? Object.entries(m.balances).map(([c, a]) => formatMoney(a, c)).join(' · ') : 'No money yet'}
                  {m.webhookUrl ? '' : ' · no callback address'}
                </p>
                <div className="chips">
                  <button onClick={() => edit(m)}>Edit</button>
                  <button onClick={() => confirm(`New keys for ${m.name}? The old key stops working at once.`) && save({ ...m, returnOrigins: m.returnOrigins, rotate: true })}>New keys</button>
                  <button onClick={() => save({ ...m, returnOrigins: m.returnOrigins, active: !m.active })}>{m.active ? 'Switch off' : 'Switch on'}</button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="card span-2">
        <h2>Latest app payments</h2>
        {!data?.payments.length ? <p className="empty">None yet.</p> : (
          <ul className="coupon-list">
            {data.payments.map(p => (
              <li key={p.id}>
                <div className="section-head">
                  <strong>{formatMoney(p.amount, p.currency)} · {p.merchant} · {p.reference}</strong>
                  <span className="badge">{p.status.replace('_', ' ')}</span>
                </div>
                <p className="muted small">
                  {formatDate(p.created)}{p.payerName ? ` · ${p.payerName}` : ''}{Number(p.refunded) > 0 ? ` · refunded ${formatMoney(p.refunded, p.currency)}` : ''}
                  {p.callbackError ? ` · callback failing (${p.callbackError})` : ''}
                </p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
