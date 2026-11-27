import { useEffect, useState } from 'react';
import { api, type Kyc } from '../lib/api';

/** Verification: a mobile number the carrier confirms is a real mobile line (the one a bank or payment app like UPI,
 * Pix or M-Pesa knows the person by), proved with a texted code. */
export default function Verify({ onNotice }: { onNotice: (m: string) => void }) {
  const [kyc, setKyc] = useState<Kyc | null>(null);
  const [needed, setNeeded] = useState(0);
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => { api.kyc().then(r => { setKyc(r.kyc); setNeeded(r.withdrawLevel); }).catch(() => {}); }, []);

  const run = (work: () => Promise<void>) => async (e: React.FormEvent) => {
    e.preventDefault();
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

  if (!kyc || kyc.status === 'verified') return null;
  return (
    <section className="card span-2 verify">
      <div className="section-head">
        <h2>Verify your mobile number</h2>
        <span className={`badge ${kyc.status === 'rejected' ? 'warn' : 'soon'}`}>{kyc.status === 'rejected' ? 'Not approved' : 'Not verified yet'}</span>
      </div>
      {needed > kyc.level && <p className="hint">Withdrawals need a verified mobile number.</p>}
      {kyc.status === 'rejected' ? (
        <p className="hint">We couldn't approve your verification{kyc.note ? `: ${kyc.note}` : ''}. Please contact Lyrpay support.</p>
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
      {kyc.status !== 'rejected' && (
        <p className="hint">Use the mobile number your bank or payment app (like UPI, Pix or M-Pesa) knows you by. We check with your carrier that it's a real mobile line; internet and virtual numbers can't be used.</p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
