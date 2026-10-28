import { useEffect, useMemo, useState } from 'react';
import { api, type Coin, type Transaction } from '../lib/api';
import { currencyName, formatDate, formatMoney, formatPrice } from '../lib/format';

const KIND_LABEL: Record<Transaction['kind'], string> = {
  topup: 'Added money', send: 'Sent', receive: 'Received', convert: 'Converted',
};

export function Activity({ transactions }: { transactions: Transaction[] }) {
  if (!transactions.length) return <p className="empty">No activity yet. Add money to get started.</p>;
  return (
    <ul className="activity">
      {transactions.map(tx => {
        const negative = tx.amount.startsWith('-');
        return (
          <li key={tx.id}>
            <div>
              <strong>{KIND_LABEL[tx.kind]}{tx.counterparty && tx.kind !== 'topup' ? ` · ${tx.counterparty}` : ''}</strong>
              <span>{formatDate(tx.at)}{tx.note ? ` · ${tx.note}` : ''}</span>
            </div>
            <div className="tx-amount">
              <strong className={negative ? '' : 'positive'}>
                {negative ? '' : '+'}{formatMoney(tx.amount, tx.currency)}
              </strong>
              {tx.kind === 'convert' && tx.toAmount && <span>+{formatMoney(tx.toAmount, tx.toCurrency!)}</span>}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function Markets({ home }: { home: string }) {
  const [coins, setCoins] = useState<Coin[] | null>(null);
  const [vs, setVs] = useState(home);
  const [error, setError] = useState('');

  useEffect(() => {
    setError('');
    // CoinGecko prices in the person's currency when it has one, else US dollars
    api.crypto(vs).then(r => setCoins(r.coins)).catch(() => (vs === 'USD' ? setError('Crypto prices are unavailable right now.') : setVs('USD')));
  }, [vs]);

  if (error) return <p className="error">{error}</p>;
  if (!coins) return <p className="empty">Loading markets…</p>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr><th>#</th><th>Asset</th><th className="num">Price</th><th className="num">24h</th><th className="num hide-sm">Market cap</th></tr></thead>
        <tbody>
          {coins.map((coin, i) => (
            <tr key={coin.id}>
              <td className="muted">{i + 1}</td>
              <td><span className="asset"><img src={coin.image} alt="" width={20} height={20} loading="lazy" />{coin.name} <span className="muted">{coin.symbol}</span></span></td>
              <td className="num">{formatPrice(coin.price, vs)}</td>
              <td className={`num ${(coin.change24h ?? 0) >= 0 ? 'positive' : 'negative'}`}>{coin.change24h?.toFixed(2) ?? '–'}%</td>
              <td className="num hide-sm">{coin.marketCap ? new Intl.NumberFormat(undefined, { style: 'currency', currency: vs, notation: 'compact' }).format(coin.marketCap) : '–'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Rates({ home, rates, updated }: { home: string; rates: Record<string, number>; updated: string }) {
  const [query, setQuery] = useState('');
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return Object.keys(rates)
      .filter(c => c !== home)
      .map(code => ({ code, name: currencyName(code), rate: rates[code] }))
      .filter(r => !q || r.code.toLowerCase().includes(q) || r.name.toLowerCase().includes(q));
  }, [rates, home, query]);

  return (
    <>
      <div className="panel-tools">
        <input type="search" placeholder="Search currency or country" value={query} onChange={e => setQuery(e.target.value)} />
        <span className="muted">Mid-market · updated {updated ? formatDate(updated) : '…'}</span>
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Currency</th><th className="num">1 {home} =</th><th className="num hide-sm">1 unit =</th></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.code}>
                <td><strong>{r.code}</strong> <span className="muted">{r.name}</span></td>
                <td className="num">{r.rate.toPrecision(6)} {r.code}</td>
                <td className="num hide-sm">{formatPrice(1 / r.rate, home)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

type Provider = { name: string; kind: string; status: 'live' | 'soon' };

// What actually moves money today vs. rails that need partner accounts and API approval first
const PROVIDERS: Provider[] = [
  { name: 'Stripe', kind: 'Cards, wallets and local methods in 135+ currencies', status: 'live' },
  { name: 'Lyrpay transfers', kind: 'Instant payments between Lyrpay accounts', status: 'live' },
  { name: 'Web3 wallets', kind: 'MetaMask, Coinbase Wallet and other browser wallets', status: 'live' },
  { name: 'PayPal', kind: 'PayPal and Venmo balances', status: 'soon' },
  { name: 'Wise', kind: 'Bank payouts in 40+ currencies', status: 'soon' },
  { name: 'Western Union', kind: 'Cash pickup in 200+ countries', status: 'soon' },
  { name: 'Ria', kind: 'Cash pickup and bank deposits', status: 'soon' },
  { name: 'Robinhood', kind: 'Stocks and crypto', status: 'soon' },
  { name: 'Kalshi', kind: 'Event contracts', status: 'soon' },
  { name: 'Gemini', kind: 'Crypto exchange', status: 'soon' },
  { name: 'Coinbase', kind: 'Crypto exchange', status: 'soon' },
  { name: 'Kraken', kind: 'Crypto exchange', status: 'soon' },
];

type Eth = { request(args: { method: string; params?: unknown[] }): Promise<unknown> };
const ethereum = () => (window as unknown as { ethereum?: Eth }).ethereum;

const CHAINS: Record<string, string> = {
  '0x1': 'Ethereum', '0x89': 'Polygon', '0x2105': 'Base', '0xa': 'Optimism', '0xa4b1': 'Arbitrum', '0x38': 'BNB Chain',
};

export function Connections() {
  const [wallet, setWallet] = useState<{ address: string; chain: string; balance: string } | null>(null);
  const [error, setError] = useState('');

  const connect = async () => {
    setError('');
    const provider = ethereum();
    if (!provider) return setError('No browser wallet found. Install MetaMask or Coinbase Wallet and reload.');
    try {
      const [address] = (await provider.request({ method: 'eth_requestAccounts' })) as string[];
      const chainId = (await provider.request({ method: 'eth_chainId' })) as string;
      const wei = BigInt((await provider.request({ method: 'eth_getBalance', params: [address, 'latest'] })) as string);
      setWallet({ address, chain: CHAINS[chainId] ?? `Chain ${parseInt(chainId, 16)}`, balance: (Number(wei) / 1e18).toFixed(5) });
    } catch (err) {
      setError((err as Error).message || 'The wallet did not connect.');
    }
  };

  return (
    <>
      <div className="card inset">
        <h3>Web3 wallet</h3>
        {wallet ? (
          <p className="wallet">
            <code>{wallet.address.slice(0, 6)}…{wallet.address.slice(-4)}</code> on {wallet.chain} · <strong>{wallet.balance}</strong> native balance
          </p>
        ) : (
          <button className="button" onClick={connect}>Connect wallet</button>
        )}
        {error && <p className="error">{error}</p>}
      </div>
      <ul className="providers">
        {PROVIDERS.map(p => (
          <li key={p.name}>
            <div><strong>{p.name}</strong><span>{p.kind}</span></div>
            <span className={`badge ${p.status}`}>{p.status === 'live' ? 'Live' : 'Coming soon'}</span>
          </li>
        ))}
      </ul>
    </>
  );
}
