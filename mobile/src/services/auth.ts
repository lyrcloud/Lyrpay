import * as SecureStore from 'expo-secure-store';
import { SITE_URL } from './config';

// Same Lyr account as the web dashboard (the pool Lyrtrip uses too), through Cognito's JSON API.
// Lyrpay's app client is public (no secret), so it's fine to ship in the app.
const REGION = 'us-east-1';
const CLIENT_ID = '38ah4ulj51l3urmn6n3gngqn3m';
const SESSION_KEY = 'lyrpay.session';
const PHONE_AUTH_URL = `${SITE_URL}/api/auth/phone`;

// Mobile-number accounts sign in with a stand-in address made from the number; it is never mailed
const phoneLoginEmail = (phone: string) => `${phone.replace(/^\+/, '')}@phone.lyrtrip.com`;

type Session = { idToken: string; refreshToken: string; expiresAt: number };

type AuthResult = { IdToken?: string; RefreshToken?: string; ExpiresIn?: number };

const MESSAGES: Record<string, string> = {
  NotAuthorizedException: 'Incorrect email, mobile number or password.',
  UserNotConfirmedException: 'Confirm your email first.',
  UsernameExistsException: 'An account already uses this email. Sign in instead.',
  CodeMismatchException: 'That code is wrong or has expired.',
  ExpiredCodeException: 'That code is wrong or has expired.',
  TooManyRequestsException: 'Too many tries. Please wait a few minutes.',
  LimitExceededException: 'Too many tries. Please wait a few minutes.',
};

// The account pool's password rules, checked before a code is sent so a rejected password doesn't use it up
export const PASSWORD_RULES = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$/;
export const PASSWORD_HINT = 'At least 8 characters with upper and lower case letters, a number and a symbol.';

async function cognito<T>(action: string, payload: Record<string, unknown>): Promise<T> {
  const response = await fetch(`https://cognito-idp.${REGION}.amazonaws.com/`, {
    method: 'POST',
    headers: {
      'content-type': 'application/x-amz-json-1.1',
      'x-amz-target': `AWSCognitoIdentityProviderService.${action}`,
    },
    body: JSON.stringify({ ClientId: CLIENT_ID, ...payload }),
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const type = String(body.__type ?? '').split('#').pop() ?? '';
    throw Object.assign(new Error(MESSAGES[type] ?? body.message ?? 'Something went wrong. Please try again.'), { name: type });
  }
  return body as T;
}

async function initiateAuth(flow: string, parameters: Record<string, string>): Promise<AuthResult> {
  const body = await cognito<{ AuthenticationResult?: AuthResult }>('InitiateAuth', { AuthFlow: flow, AuthParameters: parameters });
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

export type Login = { email: string } | { phone: string };

/** An email, or a mobile number in international format (+447700900123). */
export function parseLogin(emailOrPhone: string): Login {
  const value = emailOrPhone.trim();
  if (value.includes('@')) return { email: value.toLowerCase() };
  const phone = value.replace(/[\s().-]/g, '');
  if (!/^\+[1-9]\d{7,14}$/.test(phone)) {
    throw new Error('Enter your email, or your mobile number starting with + and the country code.');
  }
  return { phone };
}

const username = (login: Login) => ('email' in login ? login.email : phoneLoginEmail(login.phone));

export async function signIn(login: Login, password: string) {
  await save(await initiateAuth('USER_PASSWORD_AUTH', { USERNAME: username(login), PASSWORD: password }));
}

// Mobile sign-up and resets: Lyrtrip's API texts a code to the number, then creates the account (or sets its
// password), relayed by Lyrpay's API. Emails go through Cognito, which mails the code itself.
async function phoneAuth(action: 'send' | 'register' | 'reset', body: Record<string, string>) {
  const response = await fetch(`${PHONE_AUTH_URL}/${action}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Mobile sign-in is unavailable right now.');
}

/** Sends a code that confirms a new account: emailed by Cognito, or texted for a mobile number. */
export async function signUp(login: Login, password: string, name: string) {
  if ('phone' in login) return phoneAuth('send', { phone: login.phone, purpose: 'signup' });
  await cognito('SignUp', {
    Username: login.email,
    Password: password,
    UserAttributes: [{ Name: 'email', Value: login.email }, ...(name ? [{ Name: 'name', Value: name }] : [])],
  });
}

export async function resendSignUpCode(login: Login) {
  if ('phone' in login) return phoneAuth('send', { phone: login.phone, purpose: 'signup' });
  await cognito('ResendConfirmationCode', { Username: login.email });
}

/** Confirms the account with its code and signs in. */
export async function confirmSignUp(login: Login, code: string, password: string, name: string) {
  if ('phone' in login) await phoneAuth('register', { phone: login.phone, code, password, name });
  else await cognito('ConfirmSignUp', { Username: login.email, ConfirmationCode: code });
  await signIn(login, password);
}

export async function forgotPassword(login: Login) {
  if ('phone' in login) return phoneAuth('send', { phone: login.phone, purpose: 'reset' });
  await cognito('ForgotPassword', { Username: login.email });
}

/** Sets a new password with the reset code and signs in. */
export async function resetPassword(login: Login, code: string, password: string) {
  if ('phone' in login) await phoneAuth('reset', { phone: login.phone, code, password });
  else await cognito('ConfirmForgotPassword', { Username: login.email, ConfirmationCode: code, Password: password });
  await signIn(login, password);
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
