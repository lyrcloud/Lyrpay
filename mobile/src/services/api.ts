// The deployed site; CloudFront serves the API under /api on the same domain
export const SITE_URL = 'https://d2figysrc1xdl9.cloudfront.net';

export type Coin = { id: string; symbol: string; name: string; price: number; change24h: number | null };

async function request<T>(path: string): Promise<T> {
  const response = await fetch(`${SITE_URL}/api${path}`);
  if (!response.ok) {
    throw new Error(`API request failed: ${response.status}`);
  }
  return response.json();
}

export const fetchCrypto = (vs = 'usd') => request<{ vs: string; coins: Coin[] }>(`/crypto/markets?vs=${vs}`);

export const fetchRates = (base = 'USD') =>
  request<{ base: string; updated: string; rates: Record<string, number> }>(`/rates?base=${base}`);
