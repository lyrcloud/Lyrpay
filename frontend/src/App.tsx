import { useEffect, useState } from 'react';
import AuthPage from './components/AuthPage';
import Dashboard from './components/Dashboard';
import PayPage from './components/PayPage';
import { completeHostedSignIn, currentUser } from './lib/auth';
import { captureReferral } from './lib/referral';

captureReferral();

// A Lyr app's payment link (lyrpay.com/pay/lpp_…) is kept through sign-in, including Google's round trip
const PAY_KEY = 'lyrpay_pay';
const payFromPath = location.pathname.match(/^\/pay\/(lpp_[0-9a-f]{24})$/)?.[1];
try {
  if (payFromPath) sessionStorage.setItem(PAY_KEY, payFromPath);
} catch {
  // storage unavailable: the link still works when already signed in
}
const pendingPay = () => {
  try {
    return payFromPath ?? sessionStorage.getItem(PAY_KEY) ?? '';
  } catch {
    return payFromPath ?? '';
  }
};

export default function App() {
  const [signedIn, setSignedIn] = useState(() => !!currentUser());
  const [callbackError, setCallbackError] = useState('');
  const onCallback = location.pathname === '/auth/callback';
  const [payId, setPayId] = useState(pendingPay);
  const leavePay = () => {
    try { sessionStorage.removeItem(PAY_KEY); } catch { /* storage unavailable */ }
    history.replaceState(null, '', '/');
    setPayId('');
  };

  // Back from Google through the account pool's Hosted UI
  useEffect(() => {
    if (!onCallback) return;
    const params = new URLSearchParams(location.search);
    const code = params.get('code');
    const done = () => history.replaceState(null, '', '/');
    if (!code) {
      setCallbackError(params.get('error_description') || 'Google sign-in was cancelled.');
      return done();
    }
    completeHostedSignIn(code)
      .then(() => setSignedIn(true))
      .catch(err => setCallbackError(err.message))
      .finally(done);
  }, []);

  if (onCallback && !signedIn && !callbackError) return <p className="empty center-page">Signing you in…</p>;
  if (!signedIn) {
    return (
      <>
        {callbackError && <div className="toast toast-error page-toast" role="alert">{callbackError}</div>}
        {payId && <div className="toast page-toast" role="status">Sign in with your Lyr account to finish paying.</div>}
        <AuthPage onSignedIn={() => setSignedIn(true)} />
      </>
    );
  }
  if (payId) return <PayPage id={payId} onDone={leavePay} />;
  return <Dashboard onSignedOut={() => setSignedIn(false)} />;
}
