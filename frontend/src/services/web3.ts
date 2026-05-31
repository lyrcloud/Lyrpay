import { ethers } from 'ethers';

export async function connectWeb3Wallet(): Promise<string | null> {
  if (typeof window === 'undefined' || !window.ethereum) {
    alert('Please install MetaMask or another Web3 wallet.');
    return null;
  }

  try {
    const provider = new ethers.BrowserProvider(window.ethereum as any);
    await provider.send('eth_requestAccounts', []);
    const signer = await provider.getSigner();
    return await signer.getAddress();
  } catch (error) {
    console.error('Web3 connect failed', error);
    return null;
  }
}
