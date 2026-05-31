import { useEffect, useState } from 'react';
import { fetchMarketTicker, fetchWalletBalance, getTradeQuote, submitTradeOrder } from '../services/api';

interface TradingDashboardProps {
  walletAddress: string | null;
}

export default function TradingDashboard({ walletAddress }: TradingDashboardProps) {
  const [ticker, setTicker] = useState({ symbol: 'BTCUSDT', price: 0 });
  const [balance, setBalance] = useState({ USD: 0, BTC: 0 });
  const [amount, setAmount] = useState('0.01');
  const [side, setSide] = useState<'buy' | 'sell'>('buy');
  const [quote, setQuote] = useState<string>('');

  useEffect(() => {
    fetchMarketTicker('BTCUSDT').then((data) => setTicker(data));
    fetchWalletBalance().then((data) => setBalance({ USD: data.balances.USD, BTC: data.balances.BTC }));
  }, []);

  const handleQuote = async () => {
    const response = await getTradeQuote({ symbol: 'BTCUSDT', side, amount: Number(amount) });
    setQuote(`Estimated ${response.estimated_total.toFixed(2)} ${response.details.exchange}`);
  };

  const handleOrder = async () => {
    const response = await submitTradeOrder({ symbol: 'BTCUSDT', side, amount: Number(amount), slippage_tolerance: 0.005 });
    setQuote(`Order ${response.status} at ${response.price.toFixed(2)} USD`);
  };

  return (
    <section className="dashboard-card">
      <div className="dashboard-grid">
        <div className="balance-card">
          <h2>Wallet Balances</h2>
          <p>USD {balance.USD.toFixed(2)}</p>
          <p>BTC {balance.BTC}</p>
          <p>{walletAddress ? `Connected: ${walletAddress}` : 'Wallet not connected'}</p>
        </div>

        <div className="market-card">
          <h2>Market Ticker</h2>
          <p>{ticker.symbol}</p>
          <p>{ticker.price.toFixed(2)} USD</p>
        </div>
      </div>

      <div className="trade-card">
        <h2>Quick Trade</h2>
        <label>
          Side
          <select value={side} onChange={(event) => setSide(event.target.value as 'buy' | 'sell')}>
            <option value="buy">Buy</option>
            <option value="sell">Sell</option>
          </select>
        </label>
        <label>
          Amount
          <input type="number" value={amount} min="0.0001" step="0.0001" onChange={(event) => setAmount(event.target.value)} />
        </label>
        <div className="button-row">
          <button onClick={handleQuote}>Get Quote</button>
          <button onClick={handleOrder}>Submit Order</button>
        </div>
        {quote && <p className="quote-text">{quote}</p>}
      </div>
    </section>
  );
}
