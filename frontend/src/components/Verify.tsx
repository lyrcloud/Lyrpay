import { useEffect, useState } from 'react';
import { api, type Kyc } from '../lib/api';

const STATUS: Record<Kyc['status'], string> = {
  unverified: 'Not verified yet',
  phone: 'Mobile number verified',
  pending: 'Checking your ID',
  verified: 'Verified',
  rejected: 'Not approved',
};

/** Verification in two steps: a mobile number the carrier confirms is a real mobile line (the one your bank or wallet,
 * like UPI or M-Pesa, knows you by), then a photo ID with a selfie. */
export default function Verify({ onNotice }: { onNotice: (m: string) => void }) {
  const [kyc, setKyc] = useState<Kyc | null>(null);
  const [needed, setNeeded] = useState(0);
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = () => api.kyc().then(r => { setKyc(r.kyc); setNeeded(r.withdrawLevel); }).catch(() => {});
  useEffect(() => {
    load();
    // Back from Stripe's ID check
    if (new URLSearchParams(location.search).get('kyc') === 'done') {
      history.replaceState(null, '', '/');
      onNotice("Thanks! We're checking your ID; it usually takes a minute or two.");
    }
  }, []);

  const run = (work: () => Promise<void>) => async (e?: React.FormEvent) => {
    e?.preventDefault();
    setBusy(true);
    setError('');
    try {
      await work();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!kyc) return null;
  const done = kyc.level === 2;
  return (
    <section className="card span-2 verify">
      <div className="section-head">
        <h2>Verify your identity</h2>
        <span className={`badge ${done ? 'live' : kyc.status === 'rejected' ? 'warn' : 'soon'}`}>{STATUS[kyc.status]}</span>
      </div>
      {needed > kyc.level && <p className="hint">Withdrawals need {needed === 1 ? 'a verified mobile number' : 'a verified mobile number and photo ID'}.</p>}
      {kyc.status === 'rejected' && <p className="hint">We couldn't approve your verification{kyc.note ? `: ${kyc.note}` : ''}. Contact Lyrpay support.</p>}

      <ol className="verify-steps">
        <li className={kyc.phone ? 'done' : ''}>
          <strong>1. Your mobile number</strong>
          {kyc.phone ? (
            <span className="muted"> {kyc.phone.number} ✓</span>
          ) : sent ? (
            <form className="inline-form" onSubmit={run(async () => {
              const result = await api.kycCheck(code);
              setKyc(result.kyc);
              setSent(false);
              onNotice('Mobile number verified.');
            })}>
              <input inputMode="numeric" autoComplete="one-time-code" placeholder="6-digit code" value={code} onChange={e => setCode(e.target.value)} required aria-label="Code" />
              <button className="button primary" disabled={busy}>Verify</button>
              <button type="button" className="link" onClick={() => setSent(false)}>Change number</button>
            </form>
          ) : (
            <form className="inline-form" onSubmit={run(async () => { await api.kycPhone(phone); setSent(true); })}>
              <input type="tel" autoComplete="tel" placeholder="+91 98765 43210" value={phone} onChange={e => setPhone(e.target.value)} required aria-label="Mobile number" />
              <button className="button primary" disabled={busy}>Text me a code</button>
            </form>
          )}
          {!kyc.phone && <p className="hint">Use the mobile number your bank or payment app (like UPI, Pix or M-Pesa) knows you by. We check with your carrier that it's a real mobile line; internet and virtual numbers can't be used.</p>}
        </li>
        <li className={kyc.document?.status === 'verified' || done ? 'done' : ''}>
          <strong>2. Photo ID and a selfie</strong>
          {done ? <span className="muted"> ✓</span> : kyc.status === 'pending' ? (
            <span className="muted"> being checked…</span>
          ) : (
            <div className="inline-form">
              <button className="button" disabled={busy || !kyc.phone} onClick={run(async () => { location.href = (await api.kycDocument()).url; })}>
                {kyc.document?.status === 'requires_input' ? 'Try again' : 'Verify my ID'}
              </button>
            </div>
          )}
          {kyc.document?.error && !done && <p className="hint">Last try: {kyc.document.error.replace(/_/g, ' ')}.</p>}
          {!done && <p className="hint">A passport, ID card or driving licence from your country, then a quick selfie, on Stripe's secure page.</p>}
        </li>
      </ol>
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
