// A referral link (?ref=CODE) is remembered for 30 days and claimed once the person has signed in, like Lyrtrip's.
const REF_KEY = 'lyrpay:ref';
const REF_DAYS = 30;

export function captureReferral() {
  try {
    const code = new URLSearchParams(location.search).get('ref');
    if (code) localStorage.setItem(REF_KEY, JSON.stringify({ code: code.slice(0, 12), at: Date.now() }));
  } catch {
    // storage unavailable
  }
}

export function pendingReferral(): string | null {
  try {
    const saved = JSON.parse(localStorage.getItem(REF_KEY) ?? 'null');
    return saved && Date.now() - saved.at < REF_DAYS * 864e5 ? saved.code : null;
  } catch {
    return null;
  }
}

export function forgetReferral() {
  try {
    localStorage.removeItem(REF_KEY);
  } catch {
    // storage unavailable
  }
}
