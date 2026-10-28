import * as SecureStore from 'expo-secure-store';

// Same Lyr account as the web dashboard (the pool Lyrtrip uses too), through Cognito's JSON API.
// Lyrpay's app client is public (no secret), so it's fine to ship in the app.
const REGION = 'us-east-1';
const CLIENT_ID = '38ah4ulj51l3urmn6n3gngqn3m';
const SESSION_KEY = 'lyrpay.session';

// Mobile-number accounts sign in with a stand-in address made from the number; it is never mailed
const phoneLoginEmail = (phone: string) => `${phone.replace(/^\+/, '')}@phone.lyrtrip.com`;

type Session = { idToken: string; refreshToken: string; expiresAt: number };

type AuthResult = { IdToken?: string; RefreshToken?: string; ExpiresIn?: number };

const MESSAGES: Record<string, string> = {
  NotAuthorizedException: 'Incorrect email, mobile number or password.',
  UserNotConfirmedException: 'Confirm your email on lyrpay first.',
  TooManyRequestsException: 'Too many tries. Please wait a few minutes.',
  LimitExceededException: 'Too many tries. Please wait a few minutes.',
};

async function initiateAuth(flow: string, parameters: Record<string, string>): Promise<AuthResult> {
  const response = await fetch(`https://cognito-idp.${REGION}.amazonaws.com/`, {
    method: 'POST',
    headers: {
      'content-type': 'application/x-amz-json-1.1',
      'x-amz-target': 'AWSCognitoIdentityProviderService.InitiateAuth',
    },
    body: JSON.stringify({ AuthFlow: flow, ClientId: CLIENT_ID, AuthParameters: parameters }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const type = String(body.__type ?? '').split('#').pop() ?? '';
    throw Object.assign(new Error(MESSAGES[type] ?? body.message ?? 'Sign-in failed. Please try again.'), { name: type });
  }
  return body.AuthenticationResult ?? {};
}

async function save(result: AuthResult, refreshToken?: string) {
  if (!result.IdToken) throw new Error('Sign-in did not complete. Please try again.');
  const session: Session = {
    idToken: result.IdToken,
    refreshToken: result.RefreshToken ?? refreshToken ?? '',
    expiresAt: Date.now() + (result.ExpiresIn ?? 3600) * 1000,
  };
  await SecureStore.setItemAsync(SESSION_KEY, JSON.stringify(session));
}

async function load(): Promise<Session | null> {
  const raw = await SecureStore.getItemAsync(SESSION_KEY);
  return raw ? (JSON.parse(raw) as Session) : null;
}

/** Sign in with an email, or a mobile number in international format (+447700900123). */
export async function signIn(emailOrPhone: string, password: string) {
  const value = emailOrPhone.trim();
  const phone = value.replace(/[\s().-]/g, '');
  const username = value.includes('@') ? value.toLowerCase() : phoneLoginEmail(phone);
  if (!value.includes('@') && !/^\+[1-9]\d{7,14}$/.test(phone)) {
    throw new Error('Enter your email, or your mobile number starting with + and the country code.');
  }
  await save(await initiateAuth('USER_PASSWORD_AUTH', { USERNAME: username, PASSWORD: password }));
}

export async function signOut() {
  await SecureStore.deleteItemAsync(SESSION_KEY);
}

export async function isSignedIn() {
  return !!(await load());
}

/** A current ID token for API calls, renewed when it's about to expire; null when signed out. */
export async function getIdToken(): Promise<string | null> {
  const session = await load();
  if (!session) return null;
  if (session.expiresAt - Date.now() > 120_000) return session.idToken;
  try {
    await save(await initiateAuth('REFRESH_TOKEN_AUTH', { REFRESH_TOKEN: session.refreshToken }), session.refreshToken);
    return (await load())!.idToken;
  } catch (error) {
    // A refused refresh token means signed out; a network failure keeps the session for the next try
    if ((error as Error).name === 'NotAuthorizedException') {
      await signOut();
      return null;
    }
    return session.idToken;
  }
}
