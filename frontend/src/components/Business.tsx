import { useEffect, useMemo, useState } from 'react';
import { api, type Business as BusinessData, type Statement } from '../lib/api';
import { COUNTRIES, countryName, flag, formatDate, formatMoney } from '../lib/format';
import { CurrencySelect } from './MoneyActions';

type Props = {
  currencies: string[];
  balances: Record<string, string>;
  home: string;
  country: string;
  onStatement: (statement: Statement) => void;
  onNotice: (text: string) => void;
};

type Info = { business: BusinessData | null; feeBps: number; standardFeeBps: number; businessFeeBps: number; bulkMax: number };
type Line = { to: string; amount: string; note: string };

const VOLUMES: Record<string, string> = {
  'under-10k': 'Under $10,000 a month', '10k-100k': '$10,000 – $100,000', '100k-1m': '$100,000 – $1 million', 'over-1m': 'Over $1 million',
};

const pct = (bps: number) => `${(bps / 100).toFixed(bps % 100 ? 2 : 1)}%`;

function Perks({ info }: { info: Info }) {
  return (
    <ul className="perks">
      <li><span className="perk-icon">⇄</span><div><strong>{pct(info.businessFeeBps)} conversion fee</strong><span>instead of {pct(info.standardFeeBps)}, on every currency pair</span></div></li>
      <li><span className="perk-icon">⇶</span><div><strong>Bulk payments</strong><span>pay up to {info.bulkMax} suppliers, staff or partners at once</span></div></li>
      <li><span className="perk-icon">🏢</span><div><strong>Your company name</strong><span>on every payment, gift card and statement line you send</span></div></li>
      <li><span className="perk-icon">◎</span><div><strong>160+ currencies</strong><span>hold and pay in the currencies your business uses</span></div></li>
    </ul>
  );
}

function Apply({ country, onApplied }: { country: string; onApplied: (b: BusinessData) => void }) {
  const [fields, setFields] = useState({ company: '', country, registrationId: '', website: '', monthlyVolume: '', about: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const set = (key: keyof typeof fields) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setFields(f => ({ ...f, [key]: e.target.value }));

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      onApplied((await api.applyBusiness(fields)).business);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form" onSubmit={submit}>
      <label>Registered company name<input required value={fields.company} onChange={set('company')} autoComplete="organization" /></label>
      <div className="two-col">
        <label>Country of registration
          <select value={fields.country} onChange={set('country')}>
            {COUNTRIES.map(c => <option key={c.code} value={c.code}>{flag(c.code)} {c.name}</option>)}
          </select>
        </label>
        <label>Registration or tax number<input required value={fields.registrationId} onChange={set('registrationId')} placeholder="e.g. 12345678" /></label>
      </div>
      <div className="two-col">
        <label>Website<input value={fields.website} onChange={set('website')} placeholder="Optional" inputMode="url" /></label>
        <label>Expected monthly volume
          <select required value={fields.monthlyVolume} onChange={set('monthlyVolume')}>
            <option value="" disabled>Choose…</option>
            {Object.entries(VOLUMES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
      </div>
      <label>What will you use Lyrpay for?<textarea rows={3} maxLength={400} value={fields.about} onChange={set('about')} placeholder="Paying suppliers abroad, payroll, collecting from customers…" /></label>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="button primary" disabled={busy}>{busy ? 'Sending…' : 'Apply for a business account'}</button>
      <p className="hint">We review applications within 1–2 business days and email you the decision.</p>
    </form>
  );
}

/** Lines pasted from a spreadsheet: recipient, amount, note (comma, tab or semicolon separated). */
function parseLines(text: string): Line[] {
  return text.split(/\r?\n/).map(row => row.trim()).filter(Boolean)
    .map(row => row.split(/\t|;|,(?=(?:[^"]*"[^"]*")*[^"]*$)/).map(cell => cell.trim().replace(/^"|"$/g, '')))
    .filter(cells => !/^(to|recipient|email)$/i.test(cells[0]))
    .map(([to = '', amount = '', ...note]) => ({ to, amount: amount.replace(/[^\d.]/g, ''), note: note.join(' ') }));
}

function BulkPay({ info, balances, currencies, home, onStatement, onNotice }: Props & { info: Info }) {
  const held = Object.keys(balances);
  const [currency, setCurrency] = useState(held.includes(home) ? home : held[0] ?? home);
  const [lines, setLines] = useState<Line[]>([{ to: '', amount: '', note: '' }, { to: '', amount: '', note: '' }]);
  const [paste, setPaste] = useState(false);
  const [pasted, setPasted] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const filled = lines.filter(l => l.to.trim() || l.amount.trim());
  const total = useMemo(() => filled.reduce((sum, l) => sum + (Number(l.amount) || 0), 0), [filled]);
  const available = Number(balances[currency] ?? 0);
  const update = (i: number, key: keyof Line, value: string) => setLines(ls => ls.map((l, j) => (j === i ? { ...l, [key]: value } : l)));

  const usePasted = () => {
    const parsed = parseLines(pasted);
    if (parsed.length) setLines(parsed.slice(0, info.bulkMax));
    setPaste(false);
    setPasted('');
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!confirm(`Send ${formatMoney(total, currency)} to ${filled.length} ${filled.length === 1 ? 'person' : 'people'}? This can't be undone.`)) return;
    setBusy(true);
    setError('');
    try {
      const { paid, total: sent, currency: cur, ...statement } = await api.bulkPay(currency, filled);
      onStatement(statement);
      onNotice(`Paid ${paid} ${paid === 1 ? 'person' : 'people'} ${formatMoney(sent, cur)} in total.`);
      setLines([{ to: '', amount: '', note: '' }, { to: '', amount: '', note: '' }]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form" onSubmit={submit}>
      <div className="section-head">
        <CurrencySelect value={currency} onChange={setCurrency} options={held.length ? held : currencies} label="Pay in" />
        <button type="button" className="link" onClick={() => setPaste(p => !p)}>{paste ? 'Edit rows' : 'Paste from a spreadsheet'}</button>
      </div>
      {paste ? (
        <>
          <textarea rows={6} value={pasted} onChange={e => setPasted(e.target.value)} placeholder={'email or +phone, amount, note\nana@example.com, 120, Invoice 1042\n+447700900123, 80, October shifts'} />
          <button type="button" className="button" onClick={usePasted} disabled={!pasted.trim()}>Use these {parseLines(pasted).length || ''} lines</button>
        </>
      ) : (
        <div className="bulk-rows">
          <div className="bulk-row bulk-head" aria-hidden="true"><span>Recipient</span><span>Amount</span><span>Note</span><span /></div>
          {lines.map((line, i) => (
            <div className="bulk-row" key={i}>
              <input value={line.to} onChange={e => update(i, 'to', e.target.value)} placeholder="Email or +phone" aria-label={`Recipient ${i + 1}`} />
              <input value={line.amount} onChange={e => update(i, 'amount', e.target.value)} placeholder="0.00" inputMode="decimal" aria-label={`Amount ${i + 1}`} />
              <input value={line.note} onChange={e => update(i, 'note', e.target.value)} placeholder="Optional" maxLength={140} aria-label={`Note ${i + 1}`} />
              <button type="button" className="icon-button" aria-label={`Remove line ${i + 1}`} onClick={() => setLines(ls => (ls.length > 1 ? ls.filter((_, j) => j !== i) : [{ to: '', amount: '', note: '' }]))}>×</button>
            </div>
          ))}
          {lines.length < info.bulkMax && (
            <button type="button" className="link" onClick={() => setLines(ls => [...ls, { to: '', amount: '', note: '' }])}>+ Add a person</button>
          )}
        </div>
      )}
      <div className="bulk-total">
        <span>{filled.length} {filled.length === 1 ? 'payment' : 'payments'} · you have {formatMoney(available, currency)}</span>
        <strong className={total > available ? 'negative' : ''}>{formatMoney(total, currency)}</strong>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="button primary" disabled={busy || !filled.length || total <= 0}>{busy ? 'Paying…' : `Pay ${filled.length || ''} ${filled.length === 1 ? 'person' : 'people'}`}</button>
      <p className="hint">All or nothing: if any line can't be paid, nothing is sent. Each person gets the money instantly.</p>
    </form>
  );
}

export default function Business(props: Props) {
  const [info, setInfo] = useState<Info | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { api.business().then(setInfo).catch(err => setError(err.message)); }, []);

  if (error) return <section className="card"><p className="error">{error}</p></section>;
  if (!info) return <section className="card skeleton" style={{ height: 320 }} />;
  const b = info.business;

  return (
    <div className="business">
      <section className="card business-hero glow">
        <div>
          <span className="badge admin-badge">Lyrpay for Business</span>
          <h2 className="referral-title">{b?.status === 'approved' ? b.company : 'Move money across borders for your business'}</h2>
          {b?.status === 'approved' ? (
            <p className="muted">{flag(b.country)} {countryName(b.country)} · Reg. {b.registrationId} · approved {b.reviewed ? formatDate(b.reviewed) : ''}</p>
          ) : (
            <p className="muted">Lower conversion fees, bulk payments and your company name on every payment.</p>
          )}
        </div>
        <Perks info={info} />
      </section>

      {b?.status === 'approved' ? (
        <section className="card">
          <h2>Pay many people at once</h2>
          <BulkPay {...props} info={info} />
        </section>
      ) : b?.status === 'pending' ? (
        <section className="card status-card">
          <span className="status-icon pending">⏳</span>
          <div>
            <h2>We're reviewing {b.company}</h2>
            <p className="muted">You applied {formatDate(b.applied)}. We'll email you as soon as it's decided, usually within 1–2 business days.</p>
          </div>
        </section>
      ) : (
        <section className="card">
          {b?.status === 'rejected' && (
            <p className="error">We couldn't approve {b.company}{b.note ? `: ${b.note}` : '.'} You can apply again with updated details.</p>
          )}
          <h2>Apply for a business account</h2>
          <Apply country={props.country || 'US'} onApplied={business => setInfo(i => i && { ...i, business })} />
        </section>
      )}
    </div>
  );
}
