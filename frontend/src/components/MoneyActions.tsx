import { useEffect, useState } from 'react';
import { ApiError, api, type Payouts, type Statement, type TopUpMethod, type TopUpMethods, type WiseAccountType, type WiseInfo } from '../lib/api';
import { countryName, currencyName, formatMoney } from '../lib/format';

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

/** Ways to pay from the person's country, by the same rule for everyone: cards everywhere, plus the local methods
 * switched on in Stripe for that country. */
function WaysToPay({ currency, home, onWays }: { currency: string; home: string; onWays: (ways: TopUpMethods | null) => void }) {
  const [ways, setWays] = useState<TopUpMethods | null>(null);
  useEffect(() => onWays(ways), [ways]);
  useEffect(() => {
    // Follows the chosen currency, and the country: changing country also changes the home currency
    const ask = setTimeout(() => api.topupMethods(currency).then(setWays).catch(() => setWays(null)), 300);
    return () => clearTimeout(ask);
  }, [home, currency]);
  const usd = ways?.currency === currency && ways.chargedIn !== currency && (
    <p className="hint">
      Stripe can't charge in {currency}, so you'll pay the US dollar equivalent at today's mid-market rate. Your wallet
      gets the full amount in {currency}.
    </p>
  );
  const from = ways?.country ? countryName(ways.country) : '';
  let main;
  if (ways?.cardsOnly) {
    main = (
      <p className="hint">
        From {from}, pay with a Visa or Mastercard debit or credit card, or Apple Pay or Google Pay. Ask your bank to
        switch on international online payments first. Local wallets and bank transfers aren't available there yet.
      </p>
    );
  } else if (ways?.country && ways.local.length) {
    const local = ways.local.map(m => (m.currency && m.currency !== currency ? `${m.label} (pay in ${m.currency})` : m.label));
    main = <p className="hint">From {from}: {ways.cards.join(', ')}, and {local.join(', ')}. All through Stripe.</p>;
  } else {
    main = (
      <p className="hint">
        Pay by card, Apple Pay, Google Pay, bank or a local payment method in your country, through Stripe.
      {ways && !ways.country && ' Choose your country at the top of your wallet to see which ones work where you are.'}
      </p>
    );
  }
  return (
    <>
      {usd}
      {main}
    </>
  );
}

export function AddMoney({ currencies, home, cardPayments }: Props & { cardPayments: boolean }) {
  const [amount, setAmount] = useState('');
  const [currency, setCurrency] = useState(home);
  const [promo, setPromo] = useState('');
  const [promoOpen, setPromoOpen] = useState(false);
  const [bonus, setBonus] = useState<{ text: string; ok: boolean } | null>(null);
  const [ways, setWays] = useState<TopUpMethods | null>(null);
  const [method, setMethod] = useState<TopUpMethod>('stripe');
  const { busy, error, run } = useAction();
  useEffect(() => setCurrency(home), [home]);
  const paypal = !!ways?.paypal;

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
    const { url } = await api.topup(amount, currency, bonus?.ok ? promo : '', method);
    location.href = url;
  });

  return (
    <form className="form" onSubmit={submit}>
      <WaysToPay currency={currency} home={home} onWays={setWays} />
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
      <button className="button primary" disabled={busy || !cardPayments} onClick={() => setMethod('stripe')}>
        {busy && method === 'stripe' ? 'Opening checkout…' : 'Continue to payment'}
      </button>
      {paypal && (
        <button className="button paypal" disabled={busy} onClick={() => setMethod('paypal')}>
          {busy && method === 'paypal' ? 'Opening PayPal…' : 'Pay with PayPal'}
        </button>
      )}
      {paypal && ways?.currency === currency && ways.paypalChargedIn && ways.paypalChargedIn !== currency && (
        <p className="hint">PayPal can't charge in {currency}, so PayPal takes the US dollar equivalent. Your wallet gets the full amount in {currency}.</p>
      )}
      {ways?.paypalTest && <p className="hint">PayPal is in test mode (admins only): pay with a PayPal sandbox account.</p>}
      {ways?.dlocal && (
        <button className="button" disabled={busy} onClick={() => setMethod('dlocal')}>
          {busy && method === 'dlocal' ? 'Opening dLocal…' : 'Pay with local methods'}
        </button>
      )}
      {ways?.dlocal && <p className="hint">Pix, OXXO, Boleto, PSE, bank transfer, cash and local cards, through dLocal.</p>}
      {ways?.dlocalTest && <p className="hint">dLocal is in test mode (admins only): pay with card 4111 1111 1111 1111.</p>}
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

/** Wise form keys are dotted paths ("address.city"); Wise wants them nested when it refreshes the form */
function nest(flat: Record<string, string>) {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(flat)) {
    if (!value) continue;
    const parts = key.split('.');
    let node = out;
    for (const part of parts.slice(0, -1)) node = (node[part] ??= {}) as Record<string, unknown>;
    node[parts[parts.length - 1]] = value;
  }
  return out;
}

/** The bank details Wise needs for payouts in a currency, as Wise describes them: the form changes per country */
function WiseBankForm({ currency, onSaved, onCancel }: { currency: string; onSaved: (wise: WiseInfo) => void; onCancel?: () => void }) {
  const [types, setTypes] = useState<WiseAccountType[] | null>(null);
  const [type, setType] = useState('');
  const [values, setValues] = useState<Record<string, string>>({});
  const [loadError, setLoadError] = useState('');
  const { busy, error, run } = useAction();

  const load = (details?: unknown) => api.wiseRequirements(currency, details).then(r => {
    setTypes(r.types);
    setType(current => (r.types.some(t => t.type === current) ? current : r.types[0]?.type ?? ''));
  }).catch(err => setLoadError(err.message));

  useEffect(() => {
    setTypes(null);
    setValues({});
    setLoadError('');
    load();
  }, [currency]);

  const chosen = types?.find(t => t.type === type);
  const fields = chosen?.fields.flatMap(f => f.group) ?? [];

  const change = (key: string, value: string, refresh?: boolean) => {
    const next = { ...values, [key]: value };
    setValues(next);
    if (refresh) {
      const { accountHolderName, ...rest } = next;
      load({ type, accountHolderName, details: nest(rest) });
    }
  };

  const save = run(async () => {
    const picked: Record<string, string> = {};
    for (const field of fields) {
      const value = values[field.key] ?? (field.valuesAllowed?.length && field.required ? field.valuesAllowed[0].key : '');
      if (value) picked[field.key] = value;
    }
    onSaved((await api.wiseRecipient(currency, type, picked)).wise);
  });

  if (loadError) return <p className="error" role="alert">{loadError}</p>;
  if (!types) return <p className="empty">Loading Wise's form…</p>;
  if (!types.length) return <p className="empty">Wise can't pay out in {currency} yet.</p>;

  return (
    <form className="form" onSubmit={save}>
      <p className="hint">Your {currency} bank account. Wise keeps the details and pays it; Lyrpay only keeps a label.</p>
      {types.length > 1 && (
        <div className="segmented" role="group" aria-label="Kind of account">
          {types.map(t => (
            <button key={t.type} type="button" aria-pressed={t.type === type} onClick={() => setType(t.type)}>{t.title}</button>
          ))}
        </div>
      )}
      {fields.map(field => {
        const value = values[field.key] ?? '';
        const options = field.valuesAllowed ?? [];
        return (
          <label key={field.key}>{field.name}{field.required ? '' : ' (optional)'}
            {options.length ? (
              <select value={value || (field.required ? options[0].key : '')} required={field.required}
                onChange={e => change(field.key, e.target.value, field.refreshRequirementsOnChange)}>
                {!field.required && <option value="">—</option>}
                {options.map(o => <option key={o.key} value={o.key}>{o.name}</option>)}
              </select>
            ) : (
              <input type={field.type === 'date' ? 'date' : 'text'} value={value} required={field.required}
                placeholder={field.example ?? ''} pattern={field.validationRegexp ?? undefined}
                minLength={field.minLength ?? undefined} maxLength={field.maxLength ?? undefined} autoComplete="off"
                onChange={e => change(field.key, e.target.value)}
                onBlur={e => field.refreshRequirementsOnChange && change(field.key, e.target.value, true)} />
            )}
          </label>
        );
      })}
      {error && <p className="error" role="alert">{error}</p>}
      <button className="button primary" disabled={busy}>{busy ? 'Saving with Wise…' : 'Save bank account'}</button>
      {onCancel && <button type="button" className="link" onClick={onCancel}>Cancel</button>}
    </form>
  );
}

/** Withdrawals through Wise: from any balance, to a bank account in that currency, after Wise's fee */
function WiseWithdraw({ balances, onStatement, onNotice, wise, onWise }: Props & { wise: WiseInfo; onWise: (wise: WiseInfo) => void }) {
  const held = Object.keys(balances).filter(code => Number(balances[code]) > 0);
  const [currency, setCurrency] = useState(held.find(c => wise.accounts[c]) ?? held[0] ?? 'EUR');
  const [amount, setAmount] = useState('');
  const [editing, setEditing] = useState(false);
  const [quote, setQuote] = useState<{ text: string; ok: boolean } | null>(null);
  const { busy, error, run } = useAction();
  const account = wise.accounts[currency];

  useEffect(() => {
    setQuote(null);
    if (!account || !Number(amount)) return;
    const timer = setTimeout(() => api.wiseQuote(currency, amount)
      .then(q => setQuote({ ok: true, text: `${formatMoney(q.receive, currency)} arrives after Wise's ${formatMoney(q.fee, currency)} fee.${q.notice ? ` ${q.notice}` : ''}` }))
      .catch(err => setQuote({ ok: false, text: err.message })), 450);
    return () => clearTimeout(timer);
  }, [currency, amount, account]);

  const submit = run(async () => {
    const result = await api.withdraw(amount, currency, 'wise');
    onStatement(result);
    onNotice(`${formatMoney(result.withdrawn, result.currency)} is on its way to your bank through Wise. Most transfers arrive within 1–2 business days.`);
    setAmount('');
  });

  if (!held.length) return <p className="empty">Add money first, then withdraw it to your bank in any currency.</p>;
  const picker = <CurrencySelect value={currency} onChange={code => { setCurrency(code); setEditing(false); }} options={held} />;
  const testMode = wise.test && <p className="hint"><span className="badge soon">Test mode</span> Wise sandbox: no real money is sent.</p>;

  if (!account || editing) {
    return (
      <div className="form">
        {testMode}
        <label>Withdraw in {picker}</label>
        <WiseBankForm currency={currency} onSaved={info => { onWise(info); setEditing(false); }}
          onCancel={account ? () => setEditing(false) : undefined} />
      </div>
    );
  }

  return (
    <form className="form" onSubmit={submit}>
      {testMode}
      <div className="amount-row">
        <input inputMode="decimal" placeholder="0.00" required value={amount} onChange={e => setAmount(e.target.value)} aria-label="Amount" />
        {picker}
      </div>
      <p className="hint">Available: {formatMoney(balances[currency] ?? 0, currency)}. To {account}, through Wise.</p>
      {quote && <p className={quote.ok ? 'hint' : 'error'}>{quote.text}</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <button className="button primary" disabled={busy}>{busy ? 'Withdrawing…' : 'Withdraw'}</button>
      <button type="button" className="link" disabled={busy} onClick={() => setEditing(true)}>Change {currency} bank account</button>
    </form>
  );
}

export function Withdraw(props: Props & { enabled: boolean }) {
  const [payouts, setPayouts] = useState<Payouts | null>(null);
  const [rail, setRail] = useState<'wise' | 'main'>('wise');

  useEffect(() => {
    api.payouts().then(setPayouts).catch(() => setPayouts(null));
  }, [props.enabled]);

  const wise = payouts?.wise;
  if (!wise) return <MainWithdraw {...props} payouts={payouts} setPayouts={setPayouts} />;
  return (
    <>
      <div className="segmented" role="group" aria-label="Withdraw with">
        <button type="button" aria-pressed={rail === 'wise'} onClick={() => setRail('wise')}>Any currency · Wise</button>
        <button type="button" aria-pressed={rail === 'main'} onClick={() => setRail('main')}>{payouts.currency} · {payouts.provider === 'plaid' ? 'Plaid' : 'Stripe'}</button>
      </div>
      {rail === 'wise'
        ? <WiseWithdraw {...props} wise={wise} onWise={info => setPayouts({ ...payouts, wise: info })} />
        : <MainWithdraw {...props} payouts={payouts} setPayouts={setPayouts} />}
    </>
  );
}

function MainWithdraw({ balances, onStatement, onNotice, enabled, payouts, setPayouts }: Props & {
  enabled: boolean; payouts: Payouts | null; setPayouts: (payouts: Payouts | null) => void;
}) {
  const [amount, setAmount] = useState('');
  const { busy, error, run } = useAction();

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
