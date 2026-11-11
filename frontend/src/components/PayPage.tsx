import { useEffect, useState } from 'react';
import { api, type Checkout } from '../lib/api';
import { formatDate, formatMoney } from '../lib/format';
import Logo from './Logo';

/** lyrpay.com/pay/<id>: a Lyr app (Lyrtrip, Lyrshop…) asks to be paid from the person's Lyrpay balance. */
export default function PayPage({ id, onDone }: { id: string; onDone: () => void }) {
  const [checkout, setCheckout] = useState<Checkout | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<'' | 'pay' | 'cancel'>('');

  const load = () => api.checkout(id).then(c => { setCheckout(c); setError(''); }).catch(err => setError(err.message));
  useEffect(() => {
    // Signed in and on the page: the saved link has done its job, and the address bar keeps it for a refresh
    try { sessionStorage.removeItem('lyrpay_pay'); } catch { /* storage unavailable */ }
    if (location.pathname !== `/pay/${id}`) history.replaceState(null, '', `/pay/${id}`);
    load();
  }, [id]);
  // Coming back from adding money in another tab: show the new balance
  useEffect(() => {
    const again = () => document.visibilityState === 'visible' && load();
    document.addEventListener('visibilitychange', again);
    return () => document.removeEventListener('visibilitychange', again);
  }, [id]);

  const act = async (what: 'pay' | 'cancel') => {
    setBusy(what);
    setError('');
    try {
      const { redirect } = what === 'pay' ? await api.approveCheckout(id) : await api.cancelCheckout(id);
      location.href = redirect;
    } catch (err) {
      setError((err as Error).message);
      setBusy('');
      load();
    }
  };

  const open = checkout?.status === 'pending';
  return (
    <div className="pay-page">
      <header className="pay-brand"><Logo size={30} /></header>
      <section className="card pay-card">
        {!checkout && !error && <p className="empty">Loading…</p>}
        {checkout && (
          <>
            <p className="muted">Pay {checkout.merchant}</p>
            <strong className="pay-amount">{formatMoney(checkout.amount, checkout.currency)}</strong>
            {checkout.description && <p>{checkout.description}</p>}
            {open ? (
              <>
                <div className="pay-balance">
                  <span>Your {checkout.currency} balance</span>
                  <strong className={checkout.enough ? '' : 'negative'}>{formatMoney(checkout.balance, checkout.currency)}</strong>
                </div>
                {!checkout.enough && (
                  <p className="hint">
                    Not enough {checkout.currency} yet. <a href="/#wallet" target="_blank" rel="noopener">Add money or convert</a> in a new tab, then come back here.
                  </p>
                )}
                <button className="button primary" disabled={!!busy || !checkout.enough} onClick={() => act('pay')}>
                  {busy === 'pay' ? 'Paying…' : `Pay ${formatMoney(checkout.amount, checkout.currency)}`}
                </button>
                <button className="button" disabled={!!busy} onClick={() => act('cancel')}>
                  {busy === 'cancel' ? 'Going back…' : `Cancel and return to ${checkout.merchant}`}
                </button>
                <p className="hint">Open until {formatDate(checkout.expires)}. Refunds come straight back to your Lyrpay balance.</p>
              </>
            ) : (
              <>
                <p className="info">
                  {checkout.status === 'paid' && 'This payment is done.'}
                  {checkout.status === 'expired' && 'This payment has expired. Go back and start again.'}
                  {checkout.status === 'cancelled' && 'This payment was cancelled.'}
                  {(checkout.status === 'refunded' || checkout.status === 'partially_refunded') && 'This payment was refunded to your balance.'}
                </p>
                <a className="button primary" href={checkout.status === 'paid' ? checkout.returnUrl : checkout.cancelUrl}>Return to {checkout.merchant}</a>
              </>
            )}
          </>
        )}
        {error && <p className="error" role="alert">{error}</p>}
        <button className="link" onClick={onDone}>Go to my wallet</button>
      </section>
    </div>
  );
}
