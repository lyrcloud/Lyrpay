const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api';

async function request(path: string, init: RequestInit = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      Authorization: 'Bearer demo-token',
    },
    ...init,
  });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.statusText}`);
  }
  return response.json();
}

export async function fetchMarketTicker(symbol: string) {
  return request(`/market/ticker?symbol=${encodeURIComponent(symbol)}`);
}

export async function fetchWalletBalance() {
  return request('/wallet/balance');
}

export async function getTradeQuote(payload: { symbol: string; side: string; amount: number }) {
  return request('/trade/quote', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function submitTradeOrder(payload: { symbol: string; side: string; amount: number; slippage_tolerance: number }) {
  return request('/trade/order', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}
