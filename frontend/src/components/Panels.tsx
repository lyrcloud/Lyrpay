import { useState } from 'react';
import { type Transaction } from '../lib/api';
import { formatDate, formatMoney } from '../lib/format';

const KIND_LABEL: Record<Transaction['kind'], string> = {
  topup: 'Added money', send: 'Sent', receive: 'Received', convert: 'Converted', withdraw: 'Withdrew to bank',
  giftcard: 'Gift card', redeem: 'Redeemed gift card', refund: 'Refund', bonus: 'Bonus',
};

const STATUS_LABEL = { pending: 'Processing', failed: 'Failed, returned to balance', paid: '' };

export function Activity({ transactions }: { transactions: Transaction[] }) {
  if (!transactions.length) return <p className="empty">No activity yet. Add money to get started.</p>;
  return (
    <ul className="activity">
      {transactions.map(tx => {
        const negative = tx.amount.startsWith('-');
        return (
          <li key={tx.id}>
            <div>
              <strong>{KIND_LABEL[tx.kind]}{tx.counterparty && !['topup', 'withdraw'].includes(tx.kind) ? ` · ${tx.counterparty}` : ''}{tx.batch ? ' · bulk' : ''}</strong>
              <span>
                {formatDate(tx.at)}{tx.note ? ` · ${tx.note}` : ''}
                {tx.status && STATUS_LABEL[tx.status] ? ` · ${STATUS_LABEL[tx.status]}` : ''}
              </span>
            </div>
            <div className="tx-amount">
              <strong className={tx.status === 'failed' ? 'muted' : negative ? '' : 'positive'}>
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

type Provider = { name: string; kind: string; status: 'live' | 'soon' };

// What actually moves money today vs. rails that need partner accounts and API approval first
const PROVIDERS: Provider[] = [
  { name: 'Stripe', kind: 'Cards, wallets and local methods in 135+ currencies', status: 'live' },
  { name: 'Lyrpay transfers', kind: 'Instant payments between Lyrpay accounts', status: 'live' },
  { name: 'Bank withdrawals', kind: 'To your bank account via Stripe Connect', status: 'live' },
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
