import { useEffect, useState } from 'react';
import { ApiError, api, type Payouts, type Statement } from '../lib/api';
import { currencyName, formatMoney } from '../lib/format';

type Props = {
  currencies: string[];
  balances: Record<string, string>;
  home: string;
  onStatement: (statement: Statement) => void;
  onNotice: (text: string) => void;
};

export function CurrencySelect({ value, onChange, options, label = 'Currency' }: {
  value: string; onChange: (code: string) => void; options: string[]; label?: string;
}) {
  return (
    <select value={value} onChange={e => onChange(e.target.value)} aria-label={label}>
      {options.map(code => <option key={code} value={code}>{code} · {currencyName(code)}</option>)}
    </select>
  );
}

function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const run = (action: () => Promise<void>) => async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await action();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

export function AddMoney({ currencies, home, cardPayments }: Props & { cardPayments: boolean }) {
  const [amount, setAmount] = useState('');
  const [currency, setCurrency] = useState(home);
  const [promo, setPromo] = useState('');
  const [promoOpen, setPromoOpen] = useState(false);
  const [bonus, setBonus] = useState<{ text: string; ok: boolean } | null>(null);
  const { busy, error, run } = useAction();
  useEffect(() => setCurrency(home), [home]);

  // Shows the bonus a promo code adds, a moment after typing stops
  useEffect(() => {
    setBonus(null);
    if (!promo.trim() || !Number(amount)) return;
    const timer = setTimeout(() => {
      api.checkCoupon(promo, amount, currency)
        .then(r => setBonus({ ok: true, text: `+${formatMoney(r.bonus, r.currency)} bonus${r.description ? ` · ${r.description}` : ''}` }))
        .catch(err => setBonus({ ok: false, text: err.message }));
    }, 450);
    return () => clearTimeout(timer);
  }, [promo, amount, currency]);

  const submit = run(async () => {
    const { url } = await api.topup(amount, currency, bonus?.ok ? promo : '');
    location.href = url;
  });

  return (
    <form className="form" onSubmit={submit}>
      <p className="hint">
        Pay by card, Apple Pay, Google Pay, bank or a local payment method in your country, through Stripe.
      </p>
      <div className="amount-row">
        <input inputMode="decimal" placeholder="0.00" required value={amount} onChange={e => setAmount(e.target.value)} aria-label="Amount" />
        <CurrencySelect value={currency} onChange={setCurrency} options={currencies} />
      </div>
      {promoOpen ? (
        <label>Promo code
          <input value={promo} onChange={e => setPromo(e.target.value.toUpperCase())} placeholder="e.g. WELCOME10" autoComplete="off" spellCheck={false} />
          {bonus && <span className={bonus.ok ? 'promo-ok' : 'promo-bad'}>{bonus.text}</span>}
        </label>
      ) : (
        <button type="button" className="link align-start" onClick={() => setPromoOpen(true)}>Have a promo code?</button>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <button className="button primary" disabled={busy || !cardPayments}>
        {busy ? 'Opening checkout…' : 'Continue to payment'}
      </button>
      {!cardPayments && <p className="hint">Card payments are being switched on. Check back soon.</p>}
    </form>
  );
}

export function SendMoney({ balances, onStatement, onNotice }: Props) {
  const held = Object.keys(balances);
  const [to, setTo] = useState('');
  const [found, setFound] = useState<string | null>(null);
  const [amount, setAmount] = useState('');
  const [currency, setCurrency] = useState(held[0] ?? 'USD');
  const [note, setNote] = useState('');
  const { busy, error, run } = useAction();

  useEffect(() => {
    if (!held.includes(currency) && held[0]) setCurrency(held[0]);
  }, [held.join()]);

  const lookup = run(async () => setFound((await api.recipient(to)).name || to));

  const send = run(async () => {
    const result = await api.transfer(to, amount, currency, note);
    onStatement(result);
    onNotice(`Sent ${formatMoney(amount, currency)} to ${result.sentTo || to}.`);
    setTo('');
    setAmount('');
    setNote('');
    setFound(null);
  });

  if (!held.length) return <p className="empty">Add money first, then you can pay anyone on Lyrpay.</p>;

  return found === null ? (
    <form className="form" onSubmit={lookup}>
      <label>Email or mobile number
        <input required value={to} onChange={e => setTo(e.target.value)} placeholder="name@example.com or +44 7700 900123" />
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="button primary" disabled={busy}>{busy ? 'Looking up…' : 'Continue'}</button>
    </form>
  ) : (
    <form className="form" onSubmit={send}>
      <p className="recipient">Paying <strong>{found}</strong> <button type="button" className="link" onClick={() => setFound(null)}>Change</button></p>
      <div className="amount-row">
        <input inputMode="decimal" placeholder="0.00" required value={amount} onChange={e => setAmount(e.target.value)} aria-label="Amount" />
        <CurrencySelect value={currency} onChange={setCurrency} options={held} />
      </div>
      <p className="hint">Available: {formatMoney(balances[currency] ?? 0, currency)}</p>
      <label>Note (optional)<input maxLength={140} value={note} onChange={e => setNote(e.target.value)} /></label>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="button primary" disabled={busy}>{busy ? 'Sending…' : 'Send now'}</button>
    </form>
  );
}

export function Convert({ currencies, balances, home, onStatement, onNotice }: Props) {
  const held = Object.keys(balances);
  const [source, setSource] = useState(held[0] ?? home);
  const [target, setTarget] = useState(home === (held[0] ?? home) ? (home === 'EUR' ? 'USD' : 'EUR') : home);
  const [amount, setAmount] = useState('');
  const [quote, setQuote] = useState<{ rate: string; receive: string; feeBps: number } | null>(null);
  const { busy, error, run } = useAction();

  useEffect(() => {
    setQuote(null);
    if (!Number(amount) || source === target) return;
    const timer = setTimeout(() => api.quote(source, target, amount).then(setQuote).catch(() => setQuote(null)), 350);
    return () => clearTimeout(timer);
  }, [source, target, amount]);

  const submit = run(async () => {
    const result = await api.convert(source, target, amount);
    onStatement(result);
    onNotice(`Converted ${formatMoney(amount, source)} to ${formatMoney(result.receive, target)}.`);
    setAmount('');
  });

  if (!held.length) return <p className="empty">Add money first, then convert it to any currency.</p>;

  return (
    <form className="form" onSubmit={submit}>
      <label>From
        <div className="amount-row">
          <input inputMode="decimal" placeholder="0.00" required value={amount} onChange={e => setAmount(e.target.value)} aria-label="Amount" />
          <CurrencySelect value={source} onChange={setSource} options={held} label="From currency" />
        </div>
      </label>
      <p className="hint">Available: {formatMoney(balances[source] ?? 0, source)}</p>
      <label>To<CurrencySelect value={target} onChange={setTarget} options={currencies} label="To currency" /></label>
      {quote && (
        <p className="quote">
          You get <strong>{formatMoney(quote.receive, target)}</strong>
          <span>1 {source} = {Number(quote.rate).toPrecision(6)} {target} · includes {quote.feeBps / 100}% fee</span>
        </p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <button className="button primary" disabled={busy || source === target}>{busy ? 'Converting…' : 'Convert'}</button>
    </form>
  );
}

export function Withdraw({ balances, onStatement, onNotice, enabled }: Props & { enabled: boolean }) {
  const [payouts, setPayouts] = useState<Payouts | null>(null);
  const [amount, setAmount] = useState('');
  const { busy, error, run } = useAction();

  useEffect(() => {
    if (enabled) api.payouts().then(setPayouts).catch(() => setPayouts(null));
  }, [enabled]);

  const setUp = run(async () => {
    try {
      location.href = (await api.onboardPayouts()).url;
    } catch (err) {
      // 503: bank setup isn't switched on yet, so show it as coming soon rather than an error
      if (!(err instanceof ApiError && err.status === 503) || !payouts) throw err;
      setPayouts({ ...payouts, status: 'unavailable' });
    }
  });

  const plaid = payouts?.provider === 'plaid';

  const submit = run(async () => {
    const result = await api.withdraw(amount);
    onStatement(result);
    const sum = formatMoney(result.withdrawn, result.currency);
    onNotice(result.status === 'paid'
      ? `${sum} is on its way to your bank.`
      : plaid
        ? `${sum} is on its way to your bank. It usually arrives in 1–2 business days.`
        : `Your ${sum} withdrawal is processing. We'll finish it shortly; it won't be sent twice.`);
    setAmount('');
  });

  if (!enabled) return <p className="empty">Bank withdrawals are coming soon.</p>;
  if (!payouts) return <p className="empty">Loading…</p>;
  if (payouts.status === 'unavailable') return <p className="empty">Bank withdrawals are coming soon.</p>;
  const testMode = payouts.test && <p className="hint"><span className="badge soon">Test mode</span> Plaid sandbox: pick a test bank (user_good / pass_good). No real money is sent.</p>;

  if (payouts.status !== 'ready') {
    return (
      <form className="form" onSubmit={setUp}>
        {testMode}
        <p className="hint">
          {plaid
            ? 'To withdraw, add your US bank account. Plaid connects it securely; Lyrpay never sees your bank sign-in.'
            : payouts.status === 'none'
              ? 'To withdraw, add your bank account. Stripe checks your identity and keeps your bank details; Lyrpay never sees them.'
              : 'Stripe still needs a few details before you can withdraw.'}
        </p>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="button primary" disabled={busy}>
          {busy ? `Opening ${plaid ? 'Plaid' : 'Stripe'}…` : payouts.status === 'incomplete' ? 'Finish setup' : 'Add bank account'}
        </button>
      </form>
    );
  }

  const { currency } = payouts;
  return (
    <form className="form" onSubmit={submit}>
      {testMode}
      <div className="amount-row">
        <input inputMode="decimal" placeholder="0.00" required value={amount} onChange={e => setAmount(e.target.value)} aria-label="Amount" />
        <select value={currency} disabled aria-label="Currency"><option>{currency} · {currencyName(currency)}</option></select>
      </div>
      <p className="hint">
        Available: {formatMoney(balances[currency] ?? 0, currency)}. Withdrawals are paid in {currency}; convert other
        currencies first. {plaid
          ? `Sent to ${payouts.bank} by bank transfer (ACH) in 1–2 business days.`
          : 'Stripe pays out to your bank in about 2 business days.'}
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="button primary" disabled={busy}>{busy ? 'Withdrawing…' : 'Withdraw'}</button>
      <button type="button" className="link" disabled={busy} onClick={setUp}>Change bank account</button>
    </form>
  );
}
