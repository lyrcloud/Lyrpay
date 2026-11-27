import { useEffect, useState } from 'react';
import { api, type AdminAccount as Account, type AuditEntry, type Transaction } from '../lib/api';
import { countryName, flag, formatDate, formatMoney } from '../lib/format';

const KINDS: Transaction['kind'][] = ['adjust', 'topup', 'payment', 'send', 'receive', 'convert', 'withdraw', 'giftcard', 'redeem', 'refund', 'bonus', 'card'];
type Line = Account['transactions'][number];

/** One person's whole Lyrpay account, every part of it editable by the super admin. Every change is audited. */
export default function AdminAccount({ sub, onClose, onChanged }: { sub: string; onClose: () => void; onChanged: () => void }) {
  const [account, setAccount] = useState<Account | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    api.adminAccount(sub).then(r => setAccount(r.account)).catch(err => setError(err.message));
  }, [sub]);

  const act = async (work: () => Promise<{ account: Account }>, done: string) => {
    setBusy(true);
    setError('');
    try {
      setAccount((await work()).account);
      setNotice(done);
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!account) {
    return (
      <div className="drawer" role="dialog" aria-modal="true" aria-label="Account">
        <div className="drawer-panel"><button className="link" onClick={onClose}>Close</button>{error ? <p className="error">{error}</p> : <p className="empty">Loading…</p>}</div>
      </div>
    );
  }

  const p = account.profile;
  return (
    <div className="drawer" role="dialog" aria-modal="true" aria-label={`Account of ${p.name || p.email || sub}`} onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="drawer-panel">
        <div className="section-head">
          <div>
            <h2>{p.name || '—'} {p.frozen && <span className="tag warn">Frozen</span>}</h2>
            <p className="muted small">{p.email || p.phone} · {p.country ? `${flag(p.country)} ${countryName(p.country)}` : 'no country'} · joined {p.created ? formatDate(p.created) : '—'}</p>
            <p className="muted small mono">{sub}</p>
          </div>
          <button className="button" onClick={onClose}>Close</button>
        </div>
        {error && <p className="error" role="alert">{error}</p>}
        {notice && !error && <p className="promo-ok" role="status">{notice}</p>}

        <ProfileForm account={account} busy={busy} onSave={(fields, reason) => act(() => api.adminEditAccount(sub, fields, reason), 'Profile saved.')} />
        <KycPanel account={account} busy={busy} onSave={(override, note) => act(() => api.adminKyc(sub, override, note), 'Verification updated.')} />
        <Balances account={account} busy={busy} onSave={body => act(() => api.adminBalance(sub, body), 'Balance changed.')} />
        <Lines
          account={account} busy={busy}
          onAdd={(fields, rebalance) => act(() => api.adminAddLine(sub, fields, rebalance), 'Line added.')}
          onEdit={(sk, fields, rebalance) => act(() => api.adminEditLine(sub, sk, fields, rebalance), 'Line saved.')}
          onDelete={(sk, rebalance) => act(() => api.adminDeleteLine(sub, sk, rebalance), 'Line deleted.')}
        />
        <DangerZone sub={sub} busy={busy} onDeleted={() => { onChanged(); onClose(); }} onError={setError} />
      </div>
    </div>
  );
}

function ProfileForm({ account, busy, onSave }: { account: Account; busy: boolean; onSave: (fields: Record<string, unknown>, reason: string) => void }) {
  const p = account.profile;
  const [fields, setFields] = useState({ name: p.name ?? '', email: p.email ?? '', phone: p.phone ?? '', country: p.country ?? '', currency: p.currency ?? '' });
  const [frozen, setFrozen] = useState(!!p.frozen);
  const [frozenReason, setFrozenReason] = useState(p.frozenReason ?? '');
  const [reason, setReason] = useState('');
  const set = (key: keyof typeof fields) => (e: React.ChangeEvent<HTMLInputElement>) => setFields(f => ({ ...f, [key]: e.target.value }));
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const changed: Record<string, unknown> = Object.fromEntries(Object.entries(fields).filter(([k, v]) => v !== (p[k] ?? '')));
    if (frozen !== !!p.frozen) changed.frozen = frozen;
    if (frozenReason !== (p.frozenReason ?? '')) changed.frozenReason = frozenReason;
    onSave(changed, reason);
  };
  return (
    <form className="card form admin-form" onSubmit={submit}>
      <h3>Profile</h3>
      <div className="form-grid">
        <label>Name<input value={fields.name} onChange={set('name')} /></label>
        <label>Email (pays them by email)<input value={fields.email} onChange={set('email')} type="email" /></label>
        <label>Mobile (pays them by number)<input value={fields.phone} onChange={set('phone')} placeholder="+14155552671" /></label>
        <label>Country<input value={fields.country} onChange={set('country')} maxLength={2} placeholder="US" /></label>
        <label>Home currency<input value={fields.currency} onChange={set('currency')} maxLength={3} placeholder="USD" /></label>
      </div>
      <label className="check"><input type="checkbox" checked={frozen} onChange={e => setFrozen(e.target.checked)} /> Freeze: they can sign in and look, but no money moves</label>
      {frozen && <label>Why (only admins see this)<input value={frozenReason} onChange={e => setFrozenReason(e.target.value)} /></label>}
      <label>Reason for the audit log<input value={reason} onChange={e => setReason(e.target.value)} placeholder="optional" /></label>
      <p className="hint">Name, email and mobile you set here stay as set, even when their sign-in says otherwise. Their sign-in itself (shared with Lyrtrip) doesn't change.</p>
      <button className="button primary" disabled={busy}>Save profile</button>
    </form>
  );
}

const KYC_LABEL: Record<string, string> = { unverified: 'Not verified', phone: 'Mobile verified', pending: 'ID being checked', verified: 'Verified', rejected: 'Rejected' };

function KycPanel({ account, busy, onSave }: { account: Account; busy: boolean; onSave: (override: string, note: string) => void }) {
  const k = account.kyc;
  const [note, setNote] = useState(k.note);
  return (
    <section className="card admin-form">
      <h3>Verification <span className={`badge ${k.level === 2 ? 'live' : k.status === 'rejected' ? 'warn' : 'soon'}`}>{KYC_LABEL[k.status] ?? k.status}</span></h3>
      <ul className="facts">
        <li><span>Mobile</span><strong>{k.phone ? `${k.phone.number} · ${k.phone.lineType || 'line type unknown'} · ${formatDate(k.phone.at)}` : 'not verified'}</strong></li>
        <li><span>Photo ID + selfie</span><strong>{k.document ? `${k.document.status}${k.document.error ? ` (${k.document.error})` : ''} · ${formatDate(k.document.at)}` : 'not started'}</strong></li>
        <li><span>Admin decision</span><strong>{k.override || 'none (follows the checks)'}</strong></li>
      </ul>
      <label>Note<input value={note} onChange={e => setNote(e.target.value)} placeholder="why, for the audit log" /></label>
      <div className="button-row">
        <button className="button primary" disabled={busy} onClick={() => onSave('verified', note)}>Mark verified</button>
        <button className="button" disabled={busy} onClick={() => onSave('rejected', note)}>Reject</button>
        {k.override && <button className="button" disabled={busy} onClick={() => onSave('', note)}>Clear decision</button>}
      </div>
    </section>
  );
}

function Balances({ account, busy, onSave }: {
  account: Account; busy: boolean;
  onSave: (body: { currency: string; amount: string; mode: 'adjust' | 'set'; note: string; allowNegative: boolean }) => void;
}) {
  const [currency, setCurrency] = useState(Object.keys(account.balances)[0] ?? account.profile.currency ?? 'USD');
  const [amount, setAmount] = useState('');
  const [mode, setMode] = useState<'adjust' | 'set'>('adjust');
  const [note, setNote] = useState('');
  const [allowNegative, setAllowNegative] = useState(false);
  const entries = Object.entries(account.balances);
  return (
    <section className="card admin-form">
      <h3>Balances</h3>
      {entries.length ? (
        <ul className="facts">{entries.map(([c, a]) => <li key={c}><span>{c}</span><strong>{formatMoney(a, c)}</strong></li>)}</ul>
      ) : <p className="empty">No balances yet.</p>}
      <form className="form" onSubmit={e => { e.preventDefault(); onSave({ currency, amount, mode, note, allowNegative }); setAmount(''); }}>
        <div className="chips">
          <button type="button" aria-pressed={mode === 'adjust'} onClick={() => setMode('adjust')}>Add or take</button>
          <button type="button" aria-pressed={mode === 'set'} onClick={() => setMode('set')}>Set to</button>
        </div>
        <div className="amount-row">
          <input required inputMode="decimal" value={amount} onChange={e => setAmount(e.target.value)} placeholder={mode === 'adjust' ? '+10.00 or -10.00' : 'new balance'} aria-label="Amount" />
          <input value={currency} onChange={e => setCurrency(e.target.value.toUpperCase())} maxLength={3} aria-label="Currency" className="currency-input" />
        </div>
        <label>Note on their statement<input value={note} onChange={e => setNote(e.target.value)} placeholder="Balance adjustment" /></label>
        <label className="check"><input type="checkbox" checked={allowNegative} onChange={e => setAllowNegative(e.target.checked)} /> Allow the balance to go below zero</label>
        <p className="hint">They see this as an “Adjustment” line on their statement.</p>
        <button className="button primary" disabled={busy}>{mode === 'adjust' ? 'Apply' : 'Set balance'}</button>
      </form>
    </section>
  );
}

function Lines({ account, busy, onAdd, onEdit, onDelete }: {
  account: Account; busy: boolean;
  onAdd: (fields: Record<string, string>, rebalance: boolean) => void;
  onEdit: (sk: string, fields: Record<string, string>, rebalance: boolean) => void;
  onDelete: (sk: string, rebalance: boolean) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  return (
    <section className="card admin-form">
      <div className="section-head">
        <h3>Statement <span className="muted">({account.transactions.length})</span></h3>
        <button className="button" onClick={() => setAdding(a => !a)}>{adding ? 'Cancel' : 'Add a line'}</button>
      </div>
      {adding && <LineForm busy={busy} onSave={(fields, rebalance) => { onAdd(fields, rebalance); setAdding(false); }} />}
      <ul className="admin-lines">
        {account.transactions.map(tx => (
          <li key={tx.sk}>
            {editing === tx.sk ? (
              <LineForm
                line={tx} busy={busy}
                onSave={(fields, rebalance) => { onEdit(tx.sk, fields, rebalance); setEditing(null); }}
                onDelete={rebalance => { onDelete(tx.sk, rebalance); setEditing(null); }}
                onCancel={() => setEditing(null)}
              />
            ) : (
              <button className="line-row" onClick={() => setEditing(tx.sk)}>
                <span><strong>{tx.kind}</strong>{tx.counterparty ? ` · ${tx.counterparty}` : ''}{tx.note ? ` · ${tx.note}` : ''}{tx.status ? ` · ${tx.status}` : ''}</span>
                <span className="muted small">{formatDate(tx.at)}</span>
                <strong className={tx.amount.startsWith('-') ? '' : 'positive'}>{formatMoney(tx.amount, tx.currency)}</strong>
              </button>
            )}
          </li>
        ))}
      </ul>
      {!account.transactions.length && <p className="empty">No statement lines.</p>}
    </section>
  );
}

function LineForm({ line, busy, onSave, onDelete, onCancel }: {
  line?: Line; busy: boolean;
  onSave: (fields: Record<string, string>, rebalance: boolean) => void;
  onDelete?: (rebalance: boolean) => void;
  onCancel?: () => void;
}) {
  const [f, setF] = useState({
    kind: line?.kind ?? 'adjust', currency: line?.currency ?? 'USD', amount: line?.amount ?? '', status: line?.status ?? '',
    note: line?.note ?? '', counterparty: line?.counterparty ?? '', at: line?.at ?? '',
  });
  const [rebalance, setRebalance] = useState(true);
  const set = (key: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF(v => ({ ...v, [key]: e.target.value }));
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const fields = line ? Object.fromEntries(Object.entries(f).filter(([k, v]) => v !== String((line as Record<string, unknown>)[k] ?? ''))) : f;
    onSave(fields as Record<string, string>, rebalance);
  };
  return (
    <form className="form line-form" onSubmit={submit}>
      <div className="form-grid">
        <label>Kind<select value={f.kind} onChange={set('kind')}>{KINDS.map(k => <option key={k}>{k}</option>)}</select></label>
        <label>Amount (minus takes money)<input required value={f.amount} onChange={set('amount')} inputMode="decimal" /></label>
        <label>Currency<input value={f.currency} onChange={set('currency')} maxLength={3} /></label>
        <label>Status<select value={f.status} onChange={set('status')}><option value="">done</option><option>pending</option><option>paid</option><option>failed</option></select></label>
        <label>Who<input value={f.counterparty} onChange={set('counterparty')} /></label>
        <label>Note<input value={f.note} onChange={set('note')} /></label>
        <label>When (ISO time)<input value={f.at} onChange={set('at')} placeholder="now" /></label>
      </div>
      <label className="check"><input type="checkbox" checked={rebalance} onChange={e => setRebalance(e.target.checked)} /> Also move their balance by what this changes</label>
      <div className="button-row">
        <button className="button primary" disabled={busy}>{line ? 'Save line' : 'Add line'}</button>
        {onDelete && (
          <button type="button" className="button danger" disabled={busy}
            onClick={() => confirm(rebalance ? 'Delete this line and undo what it did to the balance?' : 'Delete this line only (the balance stays)?') && onDelete(rebalance)}>
            Delete
          </button>
        )}
        {onCancel && <button type="button" className="link" onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  );
}

function DangerZone({ sub, busy, onDeleted, onError }: { sub: string; busy: boolean; onDeleted: () => void; onError: (m: string) => void }) {
  const [typed, setTyped] = useState('');
  const [reason, setReason] = useState('');
  return (
    <section className="card admin-form danger-zone">
      <h3>Delete account</h3>
      <p className="hint">Removes their Lyrpay profile, balances and whole statement, and the email and number that pay them. Their Lyr sign-in stays (it's shared with Lyrtrip). The audit log keeps a copy of the profile and balances. This can't be undone.</p>
      <label>Reason<input value={reason} onChange={e => setReason(e.target.value)} /></label>
      <label>Type DELETE to confirm<input value={typed} onChange={e => setTyped(e.target.value)} autoComplete="off" /></label>
      <button className="button danger" disabled={busy || typed !== 'DELETE'}
        onClick={() => api.adminDeleteAccount(sub, reason).then(onDeleted).catch(err => onError(err.message))}>
        Delete everything
      </button>
    </section>
  );
}

/** Every admin change, newest first. */
export function AuditLog() {
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => { api.adminAudit().then(r => setEntries(r.entries)).catch(err => setError(err.message)); }, []);
  if (error) return <div className="card"><p className="error">{error}</p></div>;
  if (!entries) return <div className="card"><p className="empty">Loading…</p></div>;
  return (
    <section className="card admin-section">
      <h2>Audit log <span className="muted">(last 3 months)</span></h2>
      {entries.length ? (
        <ul className="feed">
          {entries.map(e => (
            <li key={e.sk}>
              <button className="line-row" onClick={() => setOpen(open === e.sk ? null : e.sk)}>
                <span><strong>{e.action}</strong> · {e.target}{e.reason ? ` · ${e.reason}` : ''}</span>
                <span className="muted small">{e.admin} · {formatDate(e.at)}</span>
              </button>
              {open === e.sk && <pre className="audit-json">{JSON.stringify({ before: e.before, after: e.after }, null, 2)}</pre>}
            </li>
          ))}
        </ul>
      ) : <p className="empty">No admin changes yet.</p>}
    </section>
  );
}
