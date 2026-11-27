import { useEffect, useRef, useState } from 'react';
import { api, type Transaction } from '../lib/api';
import { formatMoney, trackingNumber } from '../lib/format';
import Logo, { SiteFooter } from './Logo';

const REFRESH_MS = 10000;

type Step = { label: string; icon: string };

/** The steps a payment goes through, how far it has got, and what it's waiting on. */
function progress(tx: Transaction): { steps: Step[]; reached: number; title: string; note: string; tone: 'moving' | 'done' | 'failed' } {
  const failed = tx.status === 'failed';
  const done = !failed && (tx.status === 'paid' || !tx.status);
  if (tx.kind === 'withdraw') {
    const steps = [
      { label: 'Requested', icon: '📝' }, { label: 'Sending', icon: '🚀' },
      { label: 'With your bank', icon: '🏦' }, { label: failed ? 'Returned' : 'Paid out', icon: failed ? '↩️' : '✅' },
    ];
    if (failed) return { steps, reached: 4, tone: 'failed', title: 'Returned to your balance', note: "This withdrawal didn't go through, so the money is back on your Lyrpay balance." };
    if (done) return { steps, reached: 4, tone: 'done', title: 'Paid out', note: 'It has left Lyrpay. Banks usually show it within 1 to 3 business days.' };
    if (tx.reference) return { steps, reached: 2, tone: 'moving', title: 'With your bank', note: 'Your bank has it. Banks usually take 1 to 3 business days to show it.' };
    return {
      steps, reached: 1, tone: 'moving', title: 'Sending',
      note: tx.waiting === 'funds'
        ? "Waiting for Lyrpay's payout funds to clear. It goes out automatically, usually within 2 business days, and we'll email you."
        : "We're sending it to your bank now.",
    };
  }
  if (tx.kind === 'topup') {
    const steps = [{ label: 'Started', icon: '📝' }, { label: 'On its way', icon: '🚚' }, { label: failed ? "Bank didn't pay" : 'Added', icon: failed ? '⚠️' : '✅' }];
    if (failed) return { steps, reached: 3, tone: 'failed', title: "Your bank didn't pay", note: 'Nothing was added to your balance.' };
    if (done) return { steps, reached: 3, tone: 'done', title: 'Added to your balance', note: 'The money is on your Lyrpay balance.' };
    return { steps, reached: 1, tone: 'moving', title: 'On its way from your bank', note: 'Bank payments usually take 1 to 4 business days.' };
  }
  const steps = [{ label: 'Started', icon: '📝' }, { label: failed ? 'Failed' : 'Completed', icon: failed ? '⚠️' : '✅' }];
  return failed
    ? { steps, reached: 2, tone: 'failed', title: 'Failed', note: 'Nothing was taken from your balance.' }
    : { steps, reached: 2, tone: 'done', title: 'Completed', note: 'This payment is complete.' };
}

const KIND: Partial<Record<Transaction['kind'], string>> = {
  withdraw: 'Withdrawal to your bank', topup: 'Money added', send: 'Money sent', receive: 'Money received', convert: 'Conversion',
  payment: 'Payment', card: 'Card payment', giftcard: 'Gift card', redeem: 'Gift card redeemed', refund: 'Refund', bonus: 'Bonus', adjust: 'Adjustment',
};

function ago(since: number) {
  const s = Math.max(0, Math.round((Date.now() - since) / 1000));
  return s < 5 ? 'just now' : `${s}s ago`;
}

/** lyrpay.com/track/LP-…: one payment, followed live (the link in receipt emails). */
export default function TrackPage({ number, onDone }: { number: string; onDone: () => void }) {
  const [tx, setTx] = useState<Transaction | null>(null);
  const [error, setError] = useState('');
  const [checkedAt, setCheckedAt] = useState(0);
  const [, tick] = useState(0);
  const [copied, setCopied] = useState(false);
  const [celebrate, setCelebrate] = useState(false);
  const last = useRef('');

  useEffect(() => {
    let stop = false;
    const load = () => api.track(number).then(r => {
      if (stop) return;
      if (last.current === 'pending' && r.tx.status !== 'pending') setCelebrate(r.tx.status === 'paid');
      last.current = r.tx.status ?? '';
      setTx(r.tx);
      setError('');
      setCheckedAt(Date.now());
    }).catch(err => !stop && setError((err as Error).message));
    load();
    const timer = setInterval(() => document.visibilityState === 'visible' && last.current === 'pending' && load(), REFRESH_MS);
    const clock = setInterval(() => tick(n => n + 1), 1000);
    return () => { stop = true; clearInterval(timer); clearInterval(clock); };
  }, [number]);

  const copy = (text: string) => navigator.clipboard?.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); });
  const view = tx && progress(tx);
  const live = tx?.status === 'pending';
  const negative = tx?.amount.startsWith('-');

  return (
    <div className="track-page">
      <header className="topbar">
        <Logo />
        <span className="spacer" />
        <button className="button" onClick={onDone}>← Back to wallet</button>
      </header>
      <main className="track-main">
        {error && !tx && (
          <section className="card track-card">
            <h1>We couldn't find that payment</h1>
            <p className="muted">{error} Check the tracking number in your email: it looks like LP-XXXX-XXXX-XXXX-XXXX.</p>
            <button className="button primary" onClick={onDone}>Go to my wallet</button>
          </section>
        )}
        {!tx && !error && <section className="card track-card skeleton" style={{ height: 380 }} />}
        {tx && view && (
          <section className={`card track-card tone-${view.tone}${celebrate ? ' celebrate' : ''}`}>
            <div className="track-top">
              <span className="track-kind">{KIND[tx.kind] ?? tx.kind}</span>
              {live
                ? <span className="badge live"><span className="live-dot" /> Live · checked {ago(checkedAt)}</span>
                : <span className={`badge ${view.tone === 'failed' ? 'warn' : 'live'}`}>{view.tone === 'failed' ? 'Closed' : 'Complete'}</span>}
            </div>
            <p className={`track-amount${negative ? '' : ' positive'}`}>{negative ? '' : '+'}{formatMoney(tx.amount, tx.currency)}</p>
            {tx.counterparty && <p className="muted track-to">{negative ? 'To' : 'From'} {tx.counterparty}</p>}

            <h1 className="track-title">{view.title}</h1>
            <ol className={`track-steps steps-${view.steps.length}`} style={{ ['--progress-ratio' as string]: String(Math.min(view.reached, view.steps.length - 1) / (view.steps.length - 1)) }}>
              {view.steps.map((step, i) => (
                <li key={step.label} className={i < view.reached ? 'done' : i === view.reached ? 'now' : ''}>
                  <span className="track-dot">{i < view.reached || i === view.reached ? step.icon : ''}</span>
                  <span className="track-label">{step.label}</span>
                </li>
              ))}
            </ol>
            <p className={`track-note${view.tone === 'failed' ? ' failed' : ''}`}>{view.note}</p>

            <dl className="track-facts">
              <div><dt>Tracking number</dt><dd><button className="link mono" onClick={() => copy(trackingNumber(tx.id))} title="Copy">{trackingNumber(tx.id)} {copied ? '✓' : '⧉'}</button></dd></div>
              <div><dt>Started</dt><dd>{new Date(tx.at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}</dd></div>
              {tx.note && <div><dt>Note</dt><dd>{tx.note}</dd></div>}
            </dl>
            {live && <p className="hint center">This page updates by itself, and we'll email you when it moves.</p>}
          </section>
        )}
      </main>
      <SiteFooter />
    </div>
  );
}
