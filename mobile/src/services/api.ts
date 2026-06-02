const API_BASE = 'https://tsgo4ww25a.execute-api.us-east-1.amazonaws.com/api';

async function request(path: string, init: RequestInit = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      Authorization: 'Bearer demo-token',
    },
    ...init,
  });

  if (!response.ok) {
    throw new Error(`API request failed: ${response.statusText}`);
  }
  return response.json();
}

export async function fetchMarketTicker(symbol: string) {
  return request(`/market/ticker?symbol=${encodeURIComponent(symbol)}`);
}

export async function fetchWalletBalance() {
  return request('/wallet/balance');
}
