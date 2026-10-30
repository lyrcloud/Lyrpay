import { useEffect, useState } from 'react';
import { api, ApiError, type Profile, type Statement } from '../lib/api';
import { currentUser, signOut } from '../lib/auth';
import { COUNTRIES, currencyName, currencyOf, flag, formatMoney, guessCountry } from '../lib/format';
import Admin from './Admin';
import Business from './Business';
import Cards from './Cards';
import Rewards from './Rewards';
import { forgetReferral, pendingReferral } from '../lib/referral';
import LiveMarkets, { Ticker } from './LiveMarkets';
import Logo, { SiteFooter } from './Logo';
import { AddMoney, Convert, CurrencySelect, SendMoney, Withdraw } from './MoneyActions';
import { Activity, Connections } from './Panels';

type Tab = 'wallet' | 'cards' | 'rewards' | 'business' | 'markets' | 'connect' | 'admin';
const TABS: Tab[] = ['wallet', 'cards', 'rewards', 'business', 'markets', 'connect', 'admin'];
const TAB_LABEL: Record<Tab, string> = {
  wallet: 'Wallet', cards: 'Card', rewards: 'Rewards', business: 'Business', markets: 'Markets', connect: 'Connections', admin: '★ Admin',
};
type Action = 'add' | 'send' | 'convert' | 'withdraw';

export default function Dashboard({ onSignedOut }: { onSignedOut: () => void }) {
  const user = currentUser();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [statement, setStatement] = useState<Statement>({ balances: {}, transactions: [] });
  const [rates, setRates] = useState<{ rates: Record<string, number>; updated: string }>({ rates: {}, updated: '' });
  const [cardPayments, setCardPayments] = useState(false);
  const [admin, setAdmin] = useState(false);
  // A gift card link (?gift=CODE) opens Rewards with the code filled in
  const [giftCode] = useState(() => new URLSearchParams(location.search).get('gift') ?? '');
  // The tab is kept in the address (lyrpay.com/#admin), so it can be bookmarked and survives a reload
  const [tab, setTabState] = useState<Tab>(() => (giftCode ? 'rewards' : TABS.find(t => `#${t}` === location.hash) ?? 'wallet'));
  const setTab = (next: Tab) => {
    setTabState(next);
    history.replaceState(null, '', next === 'wallet' ? location.pathname : `#${next}`);
  };
  const [action, setAction] = useState<Action>('add');
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  const home = profile?.currency || 'USD';
  const currencies = Object.keys(rates.rates).length ? Object.keys(rates.rates).sort() : [home];

  const leave = () => {
    signOut();
    onSignedOut();
  };

  useEffect(() => {
    api.config().then(c => setCardPayments(c.cardPayments)).catch(() => {});
    api.me().then(async ({ profile, admin, ...rest }) => {
      setStatement(rest);
      setAdmin(admin);
      if (!admin) setTabState(t => (t === 'admin' ? 'wallet' : t));
      // Signed up through a friend's link: record the referral once
      const referral = pendingReferral();
      if (referral) {
        api.claimReferral(referral)
          .then(r => setNotice(`Welcome! You joined through ${r.referrer || 'a friend'}'s invite.`))
          .catch(() => {})
          .finally(forgetReferral);
      }
      // First visit: home country and currency from the browser
      if (!profile.country) {
        const country = guessCountry();
        profile = (await api.updateProfile({ country, currency: currencyOf(country) })).profile;
      }
      setProfile(profile);
    }).catch(err => (err instanceof ApiError && err.status === 401 ? leave() : setError(err.message)));

    // Back from Stripe Checkout
    const params = new URLSearchParams(location.search);
    const topup = params.get('topup');
    // Back from Stripe's bank-account setup
    const payouts = params.get('payouts');
    if (payouts) {
      history.replaceState(null, '', '/');
      setAction('withdraw');
      if (payouts === 'done') setNotice('Bank details saved with Stripe.');
    }
    if (topup) {
      history.replaceState(null, '', '/');
      if (topup === 'cancelled') setNotice('Payment cancelled. Nothing was charged.');
      else api.confirmTopup(topup).then(({ amount, currency, bonus, couponNote, pending, ...result }) => {
        setStatement(result);
        if (pending) return setNotice(`${formatMoney(amount, currency)} is on its way from your bank. It usually reaches your balance within 4 business days.`);
        const extra = Number(bonus) > 0 ? ` plus a ${formatMoney(bonus, currency)} promo bonus 🎉` : couponNote ? ` (${couponNote})` : '';
        setNotice(`${formatMoney(amount, currency)} added to your balance${extra}.`);
      }).catch(err => setError(err.message));
    }
    if (giftCode) history.replaceState(null, '', '/#rewards');
  }, []);

  useEffect(() => {
    api.rates(home).then(setRates).catch(() => {});
  }, [home]);

  const setHome = async (fields: Partial<Pick<Profile, 'currency' | 'country'>>) => {
    try {
      setProfile((await api.updateProfile(fields)).profile);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  // Everything held, valued in the home currency at today's rates
  const total = Object.entries(statement.balances).reduce(
    (sum, [code, amount]) => sum + (rates.rates[code] ? Number(amount) / rates.rates[code] : 0), 0,
  );

  const shared = { currencies, balances: statement.balances, home, onStatement: setStatement, onNotice: setNotice };

  return (
    <div className="shell">
      <header className="topbar">
        <Logo />
        <nav className="nav" aria-label="Sections">
          {TABS.filter(t => t !== 'admin' || admin).map(t => (
            <button key={t} aria-current={tab === t} onClick={() => setTab(t)} className={t === 'admin' ? 'nav-admin' : ''}>
              {TAB_LABEL[t]}
            </button>
          ))}
        </nav>
        <div className="account">
          {user?.picture ? <img src={user.picture} alt="" className="avatar" /> : <span className="avatar">{(profile?.name || user?.name || '?')[0]?.toUpperCase()}</span>}
          <span className="account-name">{profile?.name || user?.email || user?.phone}</span>
          <button className="link" onClick={leave}>Sign out</button>
        </div>
      </header>
      <Ticker />

      <main className={`main${tab === 'admin' ? ' main-wide' : ''}`}>
        {notice && <div className="toast" role="status">{notice}<button className="link" onClick={() => setNotice('')}>Dismiss</button></div>}
        {error && <div className="toast toast-error" role="alert">{error}<button className="link" onClick={() => setError('')}>Dismiss</button></div>}

        {tab === 'wallet' && (
          <div className="grid">
            <section className="card hero glow">
              <div className="hero-head">
                <span className="muted">Total balance</span>
                <div className="home-pickers">
                  <select value={profile?.country ?? ''} onChange={e => setHome({ country: e.target.value, currency: currencyOf(e.target.value) })} aria-label="Country">
                    {COUNTRIES.map(c => <option key={c.code} value={c.code}>{flag(c.code)} {c.name}</option>)}
                  </select>
                  <CurrencySelect value={home} onChange={currency => setHome({ currency })} options={currencies} label="Home currency" />
                </div>
              </div>
              <p className="total">{profile ? formatMoney(total, home) : '…'}</p>
              <ul className="balances">
                {Object.entries(statement.balances).map(([code, amount]) => (
                  <li key={code}>
                    <span className="code">{code}</span>
                    <span className="muted">{currencyName(code)}</span>
                    <strong>{formatMoney(amount, code)}</strong>
                  </li>
                ))}
                {!Object.keys(statement.balances).length && <li className="muted">Your balances in each currency will show here.</li>}
              </ul>
            </section>

            <section className="card">
              <div className="tabs" role="tablist">
                {(['add', 'send', 'convert', 'withdraw'] as Action[]).map(a => (
                  <button key={a} role="tab" aria-selected={action === a} onClick={() => setAction(a)}>
                    {{ add: 'Add', send: 'Send', convert: 'Convert', withdraw: 'Withdraw' }[a]}
                  </button>
                ))}
              </div>
              {action === 'add' && <AddMoney {...shared} cardPayments={cardPayments} />}
              {action === 'send' && <SendMoney {...shared} />}
              {action === 'convert' && <Convert {...shared} />}
              {action === 'withdraw' && <Withdraw {...shared} enabled={cardPayments} />}
            </section>

            <section className="card span-2">
              <h2>Activity</h2>
              <Activity transactions={statement.transactions} />
            </section>
          </div>
        )}

        {tab === 'cards' && <Cards balances={statement.balances} />}
        {tab === 'rewards' && <Rewards {...shared} giftCode={giftCode} />}
        {tab === 'business' && <Business {...shared} country={profile?.country ?? ''} />}
        {tab === 'markets' && <section className="card"><LiveMarkets home={home} key={home} /></section>}
        {tab === 'admin' && admin && <Admin />}
        {tab === 'connect' && <section className="card"><h2>Connections</h2><Connections /></section>}
      </main>
      <SiteFooter />
    </div>
  );
}
