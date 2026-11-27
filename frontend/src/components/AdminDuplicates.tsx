import { useEffect, useState } from 'react';
import { api, type DuplicateGroup } from '../lib/api';
import { formatMoney } from '../lib/format';
import AdminAccount from './AdminAccount';

const REASON: Record<string, string> = {
  'name+dob': 'Same name and date of birth',
  'name+phone': 'Same name and phone number',
};

/** Wallets that look like one person, for the super admin to compare and merge. Nothing is merged automatically. */
export default function AdminDuplicates() {
  const [groups, setGroups] = useState<DuplicateGroup[] | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [keep, setKeep] = useState<Record<number, string>>({});
  const [reason, setReason] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [manual, setManual] = useState({ source: '', target: '', reason: '' });

  const load = () => api.adminDuplicates().then(r => { setGroups(r.groups); setError(''); }).catch(e => setError((e as Error).message));
  useEffect(() => { load(); }, []);

  const merge = async (source: string, target: string, why: string) => {
    if (!confirm(`Move everything in account ${source} into ${target}? Its balances, points, email, phone and sign-ins go to ${target}, and ${source} is frozen. This can't be undone from here.`)) return false;
    setBusy(true);
    setError('');
    try {
      const { merged } = await api.adminMerge(source, target, why);
      const moved = Object.entries(merged.balances).map(([c, a]) => formatMoney(a, c)).join(', ') || 'no money';
      setNotice(`Merged ${source} into ${target}: moved ${moved} and ${merged.points} points.`);
      await load();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const mergeGroup = async (index: number, group: DuplicateGroup) => {
    const target = keep[index];
    for (const account of group.accounts.filter(a => a.sub !== target)) {
      if (!(await merge(account.sub, target, reason[index] ?? ''))) return;
    }
  };

  return (
    <div className="admin-section">
      <section className="card">
        <h2>Possible duplicate accounts</h2>
        <p className="muted">
          Sign-ins with the same <b>verified</b> email or phone already share one wallet automatically. These are
          different wallets that only <i>look</i> alike (same name and birthday, or name and phone). Many people share
          names, so check before merging: open an account to see its details and history.
        </p>
        {notice && <div className="toast" role="status">{notice}<button className="link" onClick={() => setNotice('')}>Dismiss</button></div>}
        {error && <p className="error">{error}</p>}
        {!groups && !error && <div className="skeleton" style={{ height: 160 }} />}
        {groups && !groups.length && <p className="empty">No look-alike accounts right now. 🎉</p>}
        <div className="dup-groups">
          {groups?.map((group, index) => (
            <div key={index} className="dup-group">
              <span className="badge warn">{REASON[group.reason] ?? group.reason}</span>
              <ul className="dup-accounts">
                {group.accounts.map(a => (
                  <li key={a.sub} className={keep[index] === a.sub ? 'keep' : ''}>
                    <label className="dup-pick">
                      <input type="radio" name={`keep-${index}`} checked={keep[index] === a.sub} onChange={() => setKeep({ ...keep, [index]: a.sub })} />
                      <span>Keep this one</span>
                    </label>
                    <strong>{a.name || '—'}</strong>
                    <span className="muted small">{a.email || 'no email'} · {a.phone || 'no phone'} · {a.country || '—'}{a.dob ? ` · born ${a.dob}` : ''}</span>
                    <span className="small">{Object.entries(a.balances).map(([c, v]) => formatMoney(v, c)).join(' · ') || 'No balance'}</span>
                    <span className="mono small muted">{a.sub}</span>
                    <button className="link" onClick={() => setOpen(a.sub)}>Open account</button>
                  </li>
                ))}
              </ul>
              <div className="dup-actions">
                <input value={reason[index] ?? ''} onChange={e => setReason({ ...reason, [index]: e.target.value })} placeholder="Why they're the same person (kept in the audit log)" />
                <button className="button primary" disabled={busy || !keep[index] || (reason[index] ?? '').trim().length < 5} onClick={() => mergeGroup(index, group)}>
                  Merge the others into the kept one
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <h2>Merge two accounts by id</h2>
        <p className="muted small">For a person you know has two accounts. Find the ids in Transactions or an account's page.</p>
        <form className="form" onSubmit={async e => { e.preventDefault(); if (await merge(manual.source.trim(), manual.target.trim(), manual.reason)) setManual({ source: '', target: '', reason: '' }); }}>
          <div className="two-col">
            <label>Merge this account (closed) <input value={manual.source} onChange={e => setManual({ ...manual, source: e.target.value })} placeholder="account id" required /></label>
            <label>Into this account (kept) <input value={manual.target} onChange={e => setManual({ ...manual, target: e.target.value })} placeholder="account id" required /></label>
          </div>
          <label>Reason <input value={manual.reason} onChange={e => setManual({ ...manual, reason: e.target.value })} placeholder="Why they're the same person" required minLength={5} /></label>
          <button className="button primary align-start" disabled={busy}>Merge</button>
        </form>
      </section>
      {open && <AdminAccount sub={open} onClose={() => setOpen(null)} onChanged={load} />}
    </div>
  );
}
