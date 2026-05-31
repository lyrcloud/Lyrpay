import { useEffect, useState } from 'react';
import TradingDashboard from './components/TradingDashboard';
import { connectWeb3Wallet } from './services/web3';

function App() {
  const [walletAddress, setWalletAddress] = useState<string | null>(null);

  useEffect(() => {
    const ethereum = (window as any).ethereum;
    if (ethereum?.on) {
      ethereum.on('accountsChanged', (accounts: string[]) => {
        setWalletAddress(accounts[0] || null);
      });
    }
  }, []);

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <h1>Lyrpay Web3 Trading</h1>
          <p>Multi-rail money, fiat, and crypto trading platform.</p>
        </div>
        <button onClick={async () => setWalletAddress(await connectWeb3Wallet())}>
          {walletAddress ? walletAddress.substring(0, 8) + '...' : 'Connect Wallet'}
        </button>
      </header>

      <main>
        <TradingDashboard walletAddress={walletAddress} />
      </main>
    </div>
  );
}

export default App;
