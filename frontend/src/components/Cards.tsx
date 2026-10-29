import { useEffect, useRef, useState } from 'react';
import { api, type Card, type CardsInfo } from '../lib/api';
import { countryName, formatMoney } from '../lib/format';
import { LogoMark } from './Logo';

type StripeJs = {
  createEphemeralKeyNonce(options: { issuingCard: string }): Promise<{ nonce?: string; error?: { message: string } }>;
  elements(): { create(type: string, options: Record<string, unknown>): { mount(target: HTMLElement): void; destroy(): void } };
};

declare global {
  interface Window { Stripe?: (key: string, options?: Record<string, unknown>) => StripeJs }
}

/** Stripe.js from Stripe's own servers: it shows card numbers in Stripe's frames, so they never reach Lyrpay. */
function loadStripe(): Promise<NonNullable<Window['Stripe']>> {
  if (window.Stripe) return Promise.resolve(window.Stripe);
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://js.stripe.com/v3/';
    script.onload = () => (window.Stripe ? resolve(window.Stripe) : reject(new Error('Stripe.js did not load.')));
    script.onerror = () => reject(new Error('Stripe.js did not load. Check your connection and try again.'));
    document.head.appendChild(script);
  });
}

/** The Lyrpay card. With `revealed` filled, Stripe's frames show the real number, expiry and CVC in place. */
function CardFace({ card, frozen, demo, slots }: {
  card?: Pick<Card, 'last4' | 'expMonth' | 'expYear' | 'name'>; frozen?: boolean; demo?: boolean;
  slots?: { number: React.RefObject<HTMLDivElement>; expiry: React.RefObject<HTMLDivElement>; cvc: React.RefObject<HTMLDivElement>; shown: boolean };
}) {
  const ref = useRef<HTMLDivElement>(null);
  const tilt = (event: React.PointerEvent) => {
    const el = ref.current;
    if (!el || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const box = el.getBoundingClientRect();
    const x = (event.clientX - box.left) / box.width - 0.5;
    const y = (event.clientY - box.top) / box.height - 0.5;
    el.style.transform = `perspective(800px) rotateY(${x * 12}deg) rotateX(${-y * 12}deg)`;
    el.style.setProperty('--shine-x', `${(x + 0.5) * 100}%`);
    el.style.setProperty('--shine-y', `${(y + 0.5) * 100}%`);
  };
  const exp = card ? `${String(card.expMonth).padStart(2, '0')}/${String(card.expYear).slice(-2)}` : '12/29';
  return (
    <div ref={ref} className={`paycard${frozen ? ' frozen' : ''}${demo ? ' demo' : ''}`} onPointerMove={tilt} onPointerLeave={() => ref.current && (ref.current.style.transform = '')}>
      <div className="paycard-top">
        <span className="giftcard-brand"><LogoMark size={28} /> Lyrpay</span>
        <span className="paycard-kind">{frozen ? '❄ Frozen' : 'Virtual'}</span>
      </div>
      <div className="paycard-chip" aria-hidden="true" />
      <div className="paycard-number">
        {slots?.shown ? <div ref={slots.number} className="stripe-slot" /> : <span>•••• •••• •••• {card?.last4 ?? '4242'}</span>}
      </div>
      <div className="paycard-bottom">
        <div><small>Cardholder</small><span>{card?.name || 'Your name'}</span></div>
        <div><small>Expires</small>{slots?.shown ? <div ref={slots.expiry} className="stripe-slot" /> : <span>{exp}</span>}</div>
        <div><small>CVC</small>{slots?.shown ? <div ref={slots.cvc} className="stripe-slot" /> : <span>•••</span>}</div>
        <span className="visa" aria-label="Visa">VISA</span>
      </div>
    </div>
  );
}

function MyCard({ card: initial, publishableKey, balance }: { card: Card; publishableKey: string; balance: string }) {
  const [card, setCard] = useState(initial);
  const [shown, setShown] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const number = useRef<HTMLDivElement>(null);
  const expiry = useRef<HTMLDivElement>(null);
  const cvc = useRef<HTMLDivElement>(null);
  const mounted = useRef<{ destroy(): void }[]>([]);

  const hide = () => {
    mounted.current.forEach(el => el.destroy());
    mounted.current = [];
    setShown(false);
  };

  useEffect(() => {
    if (!shown) return;
    let cancelled = false;
    (async () => {
      try {
        const stripe = (await loadStripe())(publishableKey);
        const { nonce, error: nonceError } = await stripe.createEphemeralKeyNonce({ issuingCard: card.cardId });
        if (!nonce) throw new Error(nonceError?.message || 'Could not open the card details.');
        const { secret } = await api.revealCard(card.cardId, nonce);
        if (cancelled) return;
        const elements = stripe.elements();
        const style = { base: { color: '#ffffff', fontSize: '17px', fontFamily: 'ui-monospace, Menlo, monospace', letterSpacing: '0.08em' } };
        const options = { issuingCard: card.cardId, nonce, ephemeralKeySecret: secret, style };
        const parts: [string, React.RefObject<HTMLDivElement>][] = [
          ['issuingCardNumberDisplay', number], ['issuingCardExpiryDisplay', expiry], ['issuingCardCvcDisplay', cvc],
        ];
        for (const [type, slot] of parts) {
          const element = elements.create(type, options);
          if (slot.current) element.mount(slot.current);
          mounted.current.push(element);
        }
        // Hidden again after a minute, so the number isn't left on screen
        setTimeout(() => !cancelled && hide(), 60_000);
      } catch (err) {
        setError((err as Error).message);
        setShown(false);
      }
    })();
    return () => { cancelled = true; };
  }, [shown]);

  const freeze = async () => {
    setBusy(true);
    setError('');
    try {
      hide();
      setCard((await api.freezeCard(card.cardId, card.status === 'active')).card);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const frozen = card.status !== 'active';
  return (
    <div className="mycard">
      <CardFace card={card} frozen={frozen} slots={{ number, expiry, cvc, shown }} />
      <div className="mycard-side">
        <div className="mycard-balance">
          <span>Spends from your balance</span>
          <strong>{formatMoney(balance, card.currency)}</strong>
          <em>{card.currency} · works online and in apps worldwide</em>
        </div>
        <div className="mycard-actions">
          <button className="button primary" disabled={frozen || busy} onClick={() => (shown ? hide() : setShown(true))}>
            {shown ? 'Hide details' : 'Show card details'}
          </button>
          <button className="button" disabled={busy} onClick={freeze}>{frozen ? 'Unfreeze card' : '❄ Freeze card'}</button>
        </div>
        {error && <p className="error">{error}</p>}
        <p className="hint">
          {frozen ? 'Frozen: every payment is declined until you unfreeze it.' : 'Payments are approved only when your balance covers them, and show up in your activity right away.'}
          {' '}Paying in another currency? Stripe converts it at the network rate.
        </p>
      </div>
    </div>
  );
}

function GetCard({ info, onIssued }: { info: CardsInfo; onIssued: (card: Card) => void }) {
  const [fields, setFields] = useState({ firstName: '', lastName: '', dob: '', line1: '', line2: '', city: '', state: '', postalCode: '' });
  const [accept, setAccept] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const set = (key: keyof typeof fields) => (e: React.ChangeEvent<HTMLInputElement>) => setFields(f => ({ ...f, [key]: e.target.value }));

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      onIssued((await api.newCard({ ...fields, acceptTerms: accept })).card);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form" onSubmit={submit}>
      <div className="two-col">
        <label>First name<input required value={fields.firstName} onChange={set('firstName')} autoComplete="given-name" /></label>
        <label>Last name<input required value={fields.lastName} onChange={set('lastName')} autoComplete="family-name" /></label>
      </div>
      <label>Date of birth<input type="date" required value={fields.dob} onChange={set('dob')} autoComplete="bday" /></label>
      <label>Billing address<input required value={fields.line1} onChange={set('line1')} placeholder="Street and number" autoComplete="address-line1" /></label>
      <input value={fields.line2} onChange={set('line2')} placeholder="Apartment, suite (optional)" autoComplete="address-line2" aria-label="Address line 2" />
      <div className="three-col">
        <label>City<input required value={fields.city} onChange={set('city')} autoComplete="address-level2" /></label>
        <label>State<input value={fields.state} onChange={set('state')} autoComplete="address-level1" /></label>
        <label>Postal code<input required value={fields.postalCode} onChange={set('postalCode')} autoComplete="postal-code" /></label>
      </div>
      <label className="check">
        <input type="checkbox" checked={accept} onChange={e => setAccept(e.target.checked)} />
        <span>I agree to the {info.termsUrl ? <a href={info.termsUrl} target="_blank" rel="noreferrer noopener">cardholder terms</a> : 'cardholder terms'} and confirm these details are mine.</span>
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="button primary" disabled={busy || !accept}>{busy ? 'Making your card…' : 'Get my virtual card'}</button>
      <p className="hint">Free. Your card is ready in seconds and works anywhere Visa is accepted online.</p>
    </form>
  );
}

export default function Cards({ balances }: { balances: Record<string, string> }) {
  const [info, setInfo] = useState<CardsInfo | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.cards().then(setInfo).catch(err => setError(err.message)); }, []);

  if (error) return <section className="card"><p className="error">{error}</p></section>;
  if (!info) return <section className="card skeleton" style={{ height: 360 }} />;

  const card = info.cards.find(c => c.status !== 'canceled');
  const join = async () => {
    setBusy(true);
    try {
      await api.joinCardWaitlist();
      setInfo(i => i && { ...i, waitlisted: true });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (card) {
    return (
      <section className="card">
        <h2>Your Lyrpay card</h2>
        <MyCard card={card} publishableKey={info.publishableKey} balance={balances[card.currency] ?? '0'} />
      </section>
    );
  }

  return (
    <section className="card cards-hero glow">
      <div className="cards-pitch">
        <span className="badge admin-badge">{info.live ? 'New' : 'Coming soon'}</span>
        <h2 className="referral-title">Your own <span className="gradient-text">Lyrpay Visa card</span></h2>
        <p className="muted">A virtual Visa card that spends straight from your Lyrpay balance. Pay online and in apps, freeze it in a tap, and see every payment instantly.</p>
        <ul className="auth-points">
          <li>No fees to get it or keep it</li>
          <li>Approved only when your balance covers it</li>
          <li>Freeze and unfreeze any time</li>
        </ul>
        {!info.live ? (
          info.waitlisted
            ? <p className="info">You're on the list. We'll email you as soon as cards are ready.</p>
            : <button className="button primary" disabled={busy} onClick={join}>Join the waitlist</button>
        ) : !info.eligible ? (
          <p className="info">Lyrpay cards are available to people living in {countryName(info.country)} first. We'll let you know when they reach your country.</p>
        ) : null}
      </div>
      <div className="cards-visual">
        <CardFace demo />
        {info.live && info.eligible && <GetCard info={info} onIssued={c => setInfo(i => i && { ...i, cards: [c, ...i.cards] })} />}
      </div>
    </section>
  );
}
