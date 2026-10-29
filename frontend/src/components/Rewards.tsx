import { useEffect, useRef, useState } from 'react';
import { api, type GiftCard, type Rewards as RewardsData, type Statement } from '../lib/api';
import { formatDate, formatMoney } from '../lib/format';
import { LogoMark } from './Logo';
import { CurrencySelect } from './MoneyActions';

type Props = {
  currencies: string[];
  balances: Record<string, string>;
  home: string;
  giftCode: string;
  onStatement: (statement: Statement) => void;
  onNotice: (text: string) => void;
};

const PRESETS = ['10', '25', '50', '100'];
const DESIGNS = ['aurora', 'sunset', 'ocean', 'midnight'] as const;
type Design = (typeof DESIGNS)[number];

export function useCopy() {
  const [copied, setCopied] = useState('');
  const copy = (text: string) => {
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(text);
      setTimeout(() => setCopied(c => (c === text ? '' : c)), 1600);
    }).catch(() => {});
  };
  return { copied, copy };
}

/** A gift card face that tilts toward the pointer. */
export function GiftCardFace({ amount, currency, to, from, code, design = 'aurora', status }: {
  amount: string; currency: string; to?: string; from?: string; code?: string; design?: Design; status?: GiftCard['status'];
}) {
  const ref = useRef<HTMLDivElement>(null);
  const tilt = (event: React.PointerEvent) => {
    const card = ref.current;
    if (!card || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const box = card.getBoundingClientRect();
    const x = (event.clientX - box.left) / box.width - 0.5;
    const y = (event.clientY - box.top) / box.height - 0.5;
    card.style.transform = `perspective(700px) rotateY(${x * 14}deg) rotateX(${-y * 14}deg)`;
    card.style.setProperty('--shine-x', `${(x + 0.5) * 100}%`);
    card.style.setProperty('--shine-y', `${(y + 0.5) * 100}%`);
  };
  const reset = () => ref.current && (ref.current.style.transform = '');
  return (
    <div ref={ref} className={`giftcard ${design}${status && status !== 'active' ? ' used' : ''}`} onPointerMove={tilt} onPointerLeave={reset}>
      <div className="giftcard-top">
        <span className="giftcard-brand"><LogoMark size={26} /> Lyrpay</span>
        <span className="giftcard-label">{status && status !== 'active' ? status : 'Gift card'}</span>
      </div>
      <strong className="giftcard-amount">{Number(amount) > 0 ? formatMoney(amount, currency) : formatMoney(0, currency)}</strong>
      <div className="giftcard-bottom">
        <span>{to ? `For ${to}` : 'For someone special'}{from ? ` · from ${from}` : ''}</span>
        <code>{code || 'LYR-••••-••••-••••'}</code>
      </div>
    </div>
  );
}

function Referral({ data }: { data: RewardsData['referral'] }) {
  const { copied, copy } = useCopy();
  const share = () => {
    const text = `Join me on Lyrpay: hold, send and convert money in 160+ currencies.`;
    if (navigator.share) navigator.share({ title: 'Lyrpay', text, url: data.link }).catch(() => {});
    else copy(data.link);
  };
  const bonus = Number(data.referrerBonusUsd);
  const friend = Number(data.friendBonusUsd);
  return (
    <section className="card referral glow">
      <span className="badge admin-badge">Refer and earn</span>
      <h2 className="referral-title">
        {bonus > 0 ? <>Earn <span className="gradient-text">{formatMoney(bonus, 'USD')}</span> for every friend</> : 'Invite your friends'}
      </h2>
      <p className="muted">
        Share your link. When a friend opens a Lyrpay wallet through it (within 30 days) and adds
        {' '}{formatMoney(data.minTopupUsd, 'USD')} or more, {bonus > 0 ? `you get ${formatMoney(bonus, 'USD')}` : 'it counts as your referral'}
        {friend > 0 ? ` and they get ${formatMoney(friend, 'USD')}` : ''}.
      </p>
      <div className="copy-row">
        <input readOnly value={data.link} onFocus={e => e.target.select()} aria-label="Your referral link" />
        <button className="button primary" onClick={() => copy(data.link)}>{copied === data.link ? 'Copied ✓' : 'Copy link'}</button>
        <button className="button" onClick={share}>Share</button>
      </div>
      <div className="referral-stats">
        <div><strong>{data.signups}</strong><span>signed up</span></div>
        <div><strong>{data.rewarded}</strong><span>qualified</span></div>
        <div><strong>{formatMoney(data.earnedUsd, 'USD')}</strong><span>earned</span></div>
        <div><button className="link" onClick={() => copy(data.code)}>{copied === data.code ? 'Copied ✓' : data.code}</button><span>your code</span></div>
      </div>
    </section>
  );
}

function BuyGiftCard({ currencies, balances, home, onStatement, onNotice, onBought }: Omit<Props, 'giftCode'> & { onBought: () => void }) {
  const held = Object.keys(balances);
  const [currency, setCurrency] = useState(held.includes(home) ? home : held[0] ?? home);
  const [amount, setAmount] = useState('25');
  const [toName, setToName] = useState('');
  const [toEmail, setToEmail] = useState('');
  const [message, setMessage] = useState('');
  const [design, setDesign] = useState<Design>('aurora');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [made, setMade] = useState<{ card: GiftCard; emailed: boolean } | null>(null);
  const { copied, copy } = useCopy();

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const { card, emailed, ...statement } = await api.buyGiftCard({ amount, currency, toEmail, toName, message });
      onStatement(statement);
      setMade({ card, emailed });
      onNotice(`Gift card for ${formatMoney(card.amount, card.currency)} is ready${emailed ? ` and on its way to ${card.toEmail}` : ''}.`);
      onBought();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (made) {
    return (
      <div className="giftcard-done">
        <GiftCardFace amount={made.card.amount} currency={made.card.currency} to={made.card.toName} code={made.card.code} design={design} />
        <p className="info">
          {made.emailed ? `We emailed it to ${made.card.toEmail}. ` : ''}Anyone with the code can redeem it into their Lyrpay balance,
          so share it only with the person it's for. You can cancel it for a refund until it's used.
        </p>
        <div className="copy-row">
          <button className="button primary" onClick={() => copy(made.card.code)}>{copied === made.card.code ? 'Copied ✓' : 'Copy code'}</button>
          <button className="button" onClick={() => setMade(null)}>Make another</button>
        </div>
      </div>
    );
  }

  return (
    <form className="giftcard-builder" onSubmit={submit}>
      <GiftCardFace amount={amount} currency={currency} to={toName} design={design} />
      <div className="form">
        <div className="design-picker" role="radiogroup" aria-label="Card design">
          {DESIGNS.map(d => <button type="button" key={d} role="radio" aria-checked={design === d} aria-label={d} className={`swatch ${d}`} onClick={() => setDesign(d)} />)}
        </div>
        <div className="chips">
          {PRESETS.map(p => <button type="button" key={p} aria-pressed={amount === p} onClick={() => setAmount(p)}>{formatMoney(p, currency)}</button>)}
        </div>
        <div className="amount-row">
          <input inputMode="decimal" required value={amount} onChange={e => setAmount(e.target.value)} aria-label="Amount" />
          <CurrencySelect value={currency} onChange={setCurrency} options={held.length ? held : currencies} label="Paid from" />
        </div>
        {balances[currency] && <p className="hint">You have {formatMoney(balances[currency], currency)}.</p>}
        <label>Their name <input value={toName} onChange={e => setToName(e.target.value)} placeholder="Optional" maxLength={60} /></label>
        <label>Email it to <input type="email" value={toEmail} onChange={e => setToEmail(e.target.value)} placeholder="Optional: or share the code yourself" /></label>
        <label>Message <textarea value={message} onChange={e => setMessage(e.target.value)} maxLength={300} rows={2} placeholder="Happy birthday!" /></label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="button primary" disabled={busy || !held.length}>{busy ? 'Making your card…' : `Buy for ${formatMoney(amount || 0, currency)}`}</button>
        {!held.length && <p className="hint">Add money first: gift cards are paid from your balance.</p>}
      </div>
    </form>
  );
}

function Redeem({ initial, onStatement, onNotice }: { initial: string } & Pick<Props, 'onStatement' | 'onNotice'>) {
  const [code, setCode] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => setCode(initial), [initial]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const { amount, currency, from, ...statement } = await api.redeemGiftCard(code);
      onStatement(statement);
      onNotice(`🎉 ${formatMoney(amount, currency)} from ${from || 'a gift card'} added to your balance.`);
      setCode('');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form" onSubmit={submit}>
      <label>Gift card code
        <input className="code-input" value={code} onChange={e => setCode(e.target.value.toUpperCase())} placeholder="LYR-XXXX-XXXX-XXXX" required autoComplete="off" spellCheck={false} />
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="button primary" disabled={busy}>{busy ? 'Redeeming…' : 'Add to my balance'}</button>
    </form>
  );
}

function BoughtCards({ cards, onStatement, onNotice, onChange }: { cards: GiftCard[]; onChange: () => void } & Pick<Props, 'onStatement' | 'onNotice'>) {
  const { copied, copy } = useCopy();
  const [busy, setBusy] = useState('');
  if (!cards.length) return <p className="empty">Gift cards you buy will show here.</p>;
  const cancel = async (card: GiftCard) => {
    if (!confirm(`Cancel this ${formatMoney(card.amount, card.currency)} gift card? The money comes back to your balance and the code stops working.`)) return;
    setBusy(card.code);
    try {
      const { amount, currency, ...statement } = await api.cancelGiftCard(card.code);
      onStatement(statement);
      onNotice(`Gift card cancelled. ${formatMoney(amount, currency)} is back on your balance.`);
      onChange();
    } catch (err) {
      onNotice((err as Error).message);
    } finally {
      setBusy('');
    }
  };
  return (
    <ul className="bought">
      {cards.map(card => (
        <li key={card.code + card.created}>
          <div>
            <strong>{formatMoney(card.amount, card.currency)}</strong>
            <span>{card.toName || card.toEmail || 'No recipient'} · {formatDate(card.created)}</span>
          </div>
          <span className={`badge ${card.status === 'active' ? 'live' : 'soon'}`}>{{ active: 'Unused', redeemed: 'Redeemed', cancelled: 'Cancelled' }[card.status]}</span>
          {card.status === 'active' && (
            <div className="bought-actions">
              <button className="link" onClick={() => copy(card.code)}>{copied === card.code ? 'Copied ✓' : 'Copy code'}</button>
              <button className="link danger" disabled={busy === card.code} onClick={() => cancel(card)}>Cancel</button>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

export default function Rewards(props: Props) {
  const [data, setData] = useState<RewardsData | null>(null);
  const [error, setError] = useState('');
  const load = () => api.rewards().then(setData).catch(err => setError(err.message));
  useEffect(() => { load(); }, []);

  if (error) return <section className="card"><p className="error">{error}</p></section>;
  if (!data) return <div className="grid"><div className="card skeleton" style={{ height: 260 }} /><div className="card skeleton" style={{ height: 260 }} /></div>;

  return (
    <div className="rewards">
      <Referral data={data.referral} />
      <div className="grid">
        <section className="card">
          <h2>Send a gift card</h2>
          <BuyGiftCard {...props} onBought={load} />
        </section>
        <div className="stack">
          <section className="card">
            <h2>Redeem a gift card</h2>
            <Redeem initial={props.giftCode} onStatement={props.onStatement} onNotice={props.onNotice} />
          </section>
          <section className="card">
            <h2>Cards you've bought</h2>
            <BoughtCards cards={data.giftCards} onStatement={props.onStatement} onNotice={props.onNotice} onChange={load} />
          </section>
        </div>
      </div>
    </div>
  );
}
