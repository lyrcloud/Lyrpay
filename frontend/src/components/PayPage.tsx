import { useEffect, useState } from 'react';
import { api, type Checkout } from '../lib/api';
import { formatDate, formatMoney } from '../lib/format';
import Logo from './Logo';

/** lyrpay.com/pay/<id>: a Lyr app (Lyrtrip, Lyrshop…) asks to be paid from the person's Lyrpay balance. */
export default function PayPage({ id, onDone }: { id: string; onDone: () => void }) {
  const [checkout, setCheckout] = useState<Checkout | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<'' | 'pay' | 'cancel' | 'topup'>('');
  const [notice, setNotice] = useState('');
  // Back from Stripe after "Add and pay": the top-up's session (or "cancelled"), read before the address is tidied
  const [topupReturn] = useState(() => new URLSearchParams(location.search).get('topup'));

  const load = () => api.checkout(id).then(c => { setCheckout(c); setError(''); }).catch(err => setError(err.message));
  useEffect(() => {
    // Signed in and on the page: the saved link has done its job, and the address bar keeps it for a refresh
    try { sessionStorage.removeItem('lyrpay_pay'); } catch { /* storage unavailable */ }
    if (location.pathname !== `/pay/${id}` || location.search) history.replaceState(null, '', `/pay/${id}`);
    if (topupReturn === 'cancelled') setNotice('Top-up cancelled. Nothing was charged.');
    if (topupReturn && topupReturn !== 'cancelled') {
      // Credit the top-up, then pay: the person chose "Add and pay"
      setBusy('pay');
      api.confirmTopup(topupReturn)
        .then(async ({ pending }) => {
          if (pending) {
            setNotice('Your top-up is on its way from the bank. Pay once it arrives.');
            setBusy('');
            return load();
          }
          const { redirect } = await api.approveCheckout(id);
          location.href = redirect;
        })
        .catch(err => { setError(err.message); setBusy(''); load(); });
      return;
    }
    load();
  }, [id]);
  // Coming back from adding money in another tab: show the new balance
  useEffect(() => {
    const again = () => document.visibilityState === 'visible' && load();
    document.addEventListener('visibilitychange', again);
    return () => document.removeEventListener('visibilitychange', again);
  }, [id]);

  const addAndPay = async () => {
    setBusy('topup');
    setError('');
    try {
      location.href = (await api.checkoutTopup(id)).url;
    } catch (err) {
      setError((err as Error).message);
      setBusy('');
    }
  };

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
                {checkout.enough ? (
                  <button className="button primary" disabled={!!busy} onClick={() => act('pay')}>
                    {busy === 'pay' ? 'Paying…' : `Pay ${formatMoney(checkout.amount, checkout.currency)}`}
                  </button>
                ) : (
                  <>
                    <button className="button primary" disabled={!!busy} onClick={addAndPay}>
                      {busy === 'topup' ? 'Opening card payment…' : busy === 'pay' ? 'Paying…'
                        : `Add ${formatMoney(Math.max(0, Number(checkout.amount) - Number(checkout.balance)), checkout.currency)} by card and pay`}
                    </button>
                    <p className="hint">
                      Card, Apple Pay or Google Pay. Or <a href="/#wallet" target="_blank" rel="noopener">add money another way</a> in a new tab and come back.
                    </p>
                  </>
                )}
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
        {notice && <p className="info">{notice}</p>}
        {error && <p className="error" role="alert">{error}</p>}
        <button className="link" onClick={onDone}>Go to my wallet</button>
      </section>
    </div>
  );
}
