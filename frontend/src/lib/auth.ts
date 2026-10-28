import {
  CognitoIdentityProviderClient,
  ConfirmForgotPasswordCommand,
  ConfirmSignUpCommand,
  ForgotPasswordCommand,
  InitiateAuthCommand,
  ResendConfirmationCodeCommand,
  SignUpCommand,
} from '@aws-sdk/client-cognito-identity-provider';

// Lyrpay signs people in with their Lyr account, the pool Lyrtrip uses too:
// email + password, mobile number + password, or Google. Same flows as
// Lyrtrip's src/lib/auth.ts.

type StoredSession = {
  accessToken: string;
  idToken: string;
  refreshToken?: string;
  expiresAt: number;
};

export type AuthenticatedUser = {
  id: string;
  email?: string;
  phone?: string;
  name?: string;
  picture?: string;
  provider: 'Email' | 'Phone' | 'Google';
};

const SESSION_KEY = 'lyrpay:session';
const PKCE_KEY = 'lyrpay:pkce-verifier';

const region = import.meta.env.VITE_COGNITO_REGION || 'us-east-1';
const clientId = import.meta.env.VITE_COGNITO_CLIENT_ID || '';
const hostedUiDomain = import.meta.env.VITE_COGNITO_DOMAIN || 'lyrtrip-auth.auth.us-east-1.amazoncognito.com';

const cognito = new CognitoIdentityProviderClient({ region });

// Mobile-number accounts sign in with a stand-in address made from the number
// (the pool only takes emails as usernames). It is never mailed.
const PHONE_LOGIN_DOMAIN = 'phone.lyrtrip.com';
export const phoneLoginEmail = (phone: string) => `${phone.replace(/^\+/, '')}@${PHONE_LOGIN_DOMAIN}`;

function readSession(): StoredSession | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as StoredSession) : null;
  } catch {
    return null;
  }
}

function saveSession(result: { AccessToken?: string; IdToken?: string; RefreshToken?: string; ExpiresIn?: number }, previous?: StoredSession) {
  if (!result.AccessToken || !result.IdToken) throw new Error('Sign-in did not complete. Please try again.');
  localStorage.setItem(SESSION_KEY, JSON.stringify({
    accessToken: result.AccessToken,
    idToken: result.IdToken,
    refreshToken: result.RefreshToken ?? previous?.refreshToken,
    expiresAt: Date.now() + (result.ExpiresIn ?? 3600) * 1000,
  }));
}

function decodeJwt(token: string): Record<string, unknown> {
  const payload = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
  return JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(payload), c => c.charCodeAt(0))));
}

export function currentUser(): AuthenticatedUser | null {
  const session = readSession();
  if (!session) return null;
  const claims = decodeJwt(session.idToken);
  const email = typeof claims.email === 'string' ? claims.email : undefined;
  const isPhone = !!email?.endsWith(`@${PHONE_LOGIN_DOMAIN}`);
  return {
    id: String(claims.sub),
    email: isPhone ? undefined : email,
    phone: typeof claims.phone_number === 'string' ? claims.phone_number : undefined,
    name: typeof claims.name === 'string' ? claims.name : undefined,
    picture: typeof claims.picture === 'string' ? claims.picture : undefined,
    provider: isPhone ? 'Phone' : claims.identities ? 'Google' : 'Email',
  };
}

let refreshing: Promise<string | null> | null = null;

/** A current ID token for API calls, renewed with the refresh token when it's about to expire. */
export async function getIdToken(): Promise<string | null> {
  const session = readSession();
  if (!session) return null;
  if (session.expiresAt - Date.now() > 120_000) return session.idToken;
  if (!session.refreshToken) {
    signOut();
    return null;
  }
  refreshing ??= (async () => {
    try {
      const response = await cognito.send(new InitiateAuthCommand({
        AuthFlow: 'REFRESH_TOKEN_AUTH',
        ClientId: clientId,
        AuthParameters: { REFRESH_TOKEN: session.refreshToken! },
      }));
      saveSession(response.AuthenticationResult ?? {}, session);
      return readSession()!.idToken;
    } catch (error) {
      // A refused refresh token means signed out; a network failure keeps the session for the next try
      if ((error as Error).name === 'NotAuthorizedException') {
        signOut();
        return null;
      }
      return session.idToken;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

export function signOut() {
  localStorage.removeItem(SESSION_KEY);
}

export async function signIn(username: string, password: string) {
  const response = await cognito.send(new InitiateAuthCommand({
    AuthFlow: 'USER_PASSWORD_AUTH',
    ClientId: clientId,
    AuthParameters: { USERNAME: username, PASSWORD: password },
  }));
  saveSession(response.AuthenticationResult ?? {});
}

export async function signUp(email: string, password: string, name: string) {
  await cognito.send(new SignUpCommand({
    ClientId: clientId,
    Username: email,
    Password: password,
    UserAttributes: [{ Name: 'email', Value: email }, ...(name ? [{ Name: 'name', Value: name }] : [])],
  }));
}

export async function confirmSignUp(email: string, code: string) {
  await cognito.send(new ConfirmSignUpCommand({ ClientId: clientId, Username: email, ConfirmationCode: code }));
}

export async function resendConfirmation(email: string) {
  await cognito.send(new ResendConfirmationCodeCommand({ ClientId: clientId, Username: email }));
}

export async function forgotPassword(email: string) {
  await cognito.send(new ForgotPasswordCommand({ ClientId: clientId, Username: email }));
}

export async function confirmForgotPassword(email: string, code: string, password: string) {
  await cognito.send(new ConfirmForgotPasswordCommand({
    ClientId: clientId, Username: email, ConfirmationCode: code, Password: password,
  }));
}

// Mobile sign-up and resets: a code is texted to the number, then the account is created
// (or its password set) and the person signs in like any other account.
async function phoneAuth(action: 'send' | 'register' | 'reset', body: Record<string, string>) {
  const response = await fetch(`/api/auth/phone/${action}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail || 'Mobile sign-in is unavailable right now.');
}

export const sendPhoneCode = (phone: string, purpose: 'signup' | 'reset') => phoneAuth('send', { phone, purpose });
export const registerPhone = (phone: string, code: string, password: string, name: string) =>
  phoneAuth('register', { phone, code, password, name });
export const resetPhonePassword = (phone: string, code: string, password: string) =>
  phoneAuth('reset', { phone, code, password });

// Google, through the pool's Hosted UI with PKCE (the site has no server to keep a client secret)

function base64Url(bytes: Uint8Array) {
  let binary = '';
  bytes.forEach(b => (binary += String.fromCharCode(b)));
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

const redirectUri = () => `${location.origin}/auth/callback`;

export async function signInWithGoogle() {
  const verifier = base64Url(crypto.getRandomValues(new Uint8Array(48)));
  const challenge = base64Url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))));
  sessionStorage.setItem(PKCE_KEY, verifier);
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: clientId,
    redirect_uri: redirectUri(),
    identity_provider: 'Google',
    scope: 'openid email profile phone',
    code_challenge: challenge,
    code_challenge_method: 'S256',
  });
  location.href = `https://${hostedUiDomain}/oauth2/authorize?${params}`;
}

export async function completeHostedSignIn(code: string) {
  const verifier = sessionStorage.getItem(PKCE_KEY) || '';
  sessionStorage.removeItem(PKCE_KEY);
  const response = await fetch(`https://${hostedUiDomain}/oauth2/token`, {
    method: 'POST',
    headers: { 'content-type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code', client_id: clientId, code, redirect_uri: redirectUri(), code_verifier: verifier,
    }),
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error_description || body.error || 'Google sign-in failed.');
  saveSession({ AccessToken: body.access_token, IdToken: body.id_token, RefreshToken: body.refresh_token, ExpiresIn: body.expires_in });
}
