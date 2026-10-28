import { useEffect, useState } from 'react';
import { api, ApiError, type Profile, type Statement } from '../lib/api';
import { currentUser, signOut } from '../lib/auth';
import { COUNTRIES, currencyName, currencyOf, flag, formatMoney, guessCountry } from '../lib/format';
import { AddMoney, Convert, CurrencySelect, SendMoney } from './MoneyActions';
import { Activity, Connections, Markets, Rates } from './Panels';

type Tab = 'wallet' | 'markets' | 'rates' | 'connect';
type Action = 'add' | 'send' | 'convert';

export default function Dashboard({ onSignedOut }: { onSignedOut: () => void }) {
  const user = currentUser();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [statement, setStatement] = useState<Statement>({ balances: {}, transactions: [] });
  const [rates, setRates] = useState<{ rates: Record<string, number>; updated: string }>({ rates: {}, updated: '' });
  const [cardPayments, setCardPayments] = useState(false);
  const [tab, setTab] = useState<Tab>('wallet');
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
    api.me().then(async ({ profile, ...rest }) => {
      setStatement(rest);
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
    if (topup) {
      history.replaceState(null, '', '/');
      if (topup === 'cancelled') setNotice('Payment cancelled. Nothing was charged.');
      else api.confirmTopup(topup).then(result => {
        setStatement(result);
        setNotice(`${formatMoney(result.amount, result.currency)} added to your balance.`);
      }).catch(err => setError(err.message));
    }
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
        <div className="brand"><span className="logo">L</span> Lyrpay</div>
        <nav className="nav" aria-label="Sections">
          {(['wallet', 'markets', 'rates', 'connect'] as Tab[]).map(t => (
            <button key={t} aria-current={tab === t} onClick={() => setTab(t)}>
              {{ wallet: 'Wallet', markets: 'Crypto', rates: 'World rates', connect: 'Connections' }[t]}
            </button>
          ))}
        </nav>
        <div className="account">
          {user?.picture ? <img src={user.picture} alt="" className="avatar" /> : <span className="avatar">{(profile?.name || user?.name || '?')[0]?.toUpperCase()}</span>}
          <span className="account-name">{profile?.name || user?.email || user?.phone}</span>
          <button className="link" onClick={leave}>Sign out</button>
        </div>
      </header>

      <main className="main">
        {notice && <div className="toast" role="status">{notice}<button className="link" onClick={() => setNotice('')}>Dismiss</button></div>}
        {error && <div className="toast toast-error" role="alert">{error}<button className="link" onClick={() => setError('')}>Dismiss</button></div>}

        {tab === 'wallet' && (
          <div className="grid">
            <section className="card hero">
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
                {(['add', 'send', 'convert'] as Action[]).map(a => (
                  <button key={a} role="tab" aria-selected={action === a} onClick={() => setAction(a)}>
                    {{ add: 'Add money', send: 'Send', convert: 'Convert' }[a]}
                  </button>
                ))}
              </div>
              {action === 'add' && <AddMoney {...shared} cardPayments={cardPayments} />}
              {action === 'send' && <SendMoney {...shared} />}
              {action === 'convert' && <Convert {...shared} />}
            </section>

            <section className="card span-2">
              <h2>Activity</h2>
              <Activity transactions={statement.transactions} />
            </section>
          </div>
        )}

        {tab === 'markets' && <section className="card"><h2>Crypto markets</h2><Markets home={home} /></section>}
        {tab === 'rates' && <section className="card"><h2>Exchange rates for {currencyName(home)}</h2><Rates home={home} {...rates} /></section>}
        {tab === 'connect' && <section className="card"><h2>Connections</h2><Connections /></section>}
      </main>
    </div>
  );
}
