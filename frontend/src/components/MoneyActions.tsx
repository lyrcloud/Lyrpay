import { useEffect, useState } from 'react';
import { api, type Statement } from '../lib/api';
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
  const { busy, error, run } = useAction();
  useEffect(() => setCurrency(home), [home]);

  const submit = run(async () => {
    const { url } = await api.topup(amount, currency);
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
