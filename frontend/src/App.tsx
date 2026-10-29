import { useEffect, useState } from 'react';
import AuthPage from './components/AuthPage';
import Dashboard from './components/Dashboard';
import { completeHostedSignIn, currentUser } from './lib/auth';
import { captureReferral } from './lib/referral';

captureReferral();

export default function App() {
  const [signedIn, setSignedIn] = useState(() => !!currentUser());
  const [callbackError, setCallbackError] = useState('');
  const onCallback = location.pathname === '/auth/callback';

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
        <AuthPage onSignedIn={() => setSignedIn(true)} />
      </>
    );
  }
  return <Dashboard onSignedOut={() => setSignedIn(false)} />;
}
