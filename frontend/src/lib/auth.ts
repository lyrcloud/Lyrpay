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
  clientId?: string; // which Lyr sign-in issued it, for refreshing
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
// Lyrcloud accounts (Lyrcloud, Lyrsoft) sign in too, through Lyrpay's app client in the Lyrcloud pool. The API maps
// both kinds of sign-in to one wallet per person (backend/app/identity.py).
const lyrcloudClientId = import.meta.env.VITE_LYRCLOUD_CLIENT_ID || '400r1qrclm20r9ni9s69de6tr6';
const lyrcloud = new CognitoIdentityProviderClient({ region });
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

function saveSession(result: { AccessToken?: string; IdToken?: string; RefreshToken?: string; ExpiresIn?: number }, previous?: StoredSession, client = previous?.clientId ?? clientId) {
  if (!result.AccessToken || !result.IdToken) throw new Error('Sign-in did not complete. Please try again.');
  localStorage.setItem(SESSION_KEY, JSON.stringify({
    accessToken: result.AccessToken,
    idToken: result.IdToken,
    refreshToken: result.RefreshToken ?? previous?.refreshToken,
    expiresAt: Date.now() + (result.ExpiresIn ?? 3600) * 1000,
    clientId: client,
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
      const fromLyrcloud = session.clientId === lyrcloudClientId;
      const response = await (fromLyrcloud ? lyrcloud : cognito).send(new InitiateAuthCommand({
        AuthFlow: 'REFRESH_TOKEN_AUTH',
        ClientId: fromLyrcloud ? lyrcloudClientId : clientId,
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

/** One login for the Lyr apps: a Lyr account first, then the same email and password as a Lyrcloud (or Lyrsoft)
 * account, so either signs in here with nothing new to create. */
export async function signIn(username: string, password: string) {
  try {
    const response = await cognito.send(new InitiateAuthCommand({
      AuthFlow: 'USER_PASSWORD_AUTH',
      ClientId: clientId,
      AuthParameters: { USERNAME: username, PASSWORD: password },
    }));
    saveSession(response.AuthenticationResult ?? {}, undefined, clientId);
  } catch (error) {
    const name = (error as Error).name;
    if (!lyrcloudClientId || !['NotAuthorizedException', 'UserNotFoundException'].includes(name) || !username.includes('@')) throw error;
    try {
      const response = await lyrcloud.send(new InitiateAuthCommand({
        AuthFlow: 'USER_PASSWORD_AUTH',
        ClientId: lyrcloudClientId,
        AuthParameters: { USERNAME: username.trim().toLowerCase(), PASSWORD: password },
      }));
      saveSession(response.AuthenticationResult ?? {}, undefined, lyrcloudClientId);
    } catch {
      throw error; // neither knows these details: the Lyr account's own message
    }
  }
}

// The account pool's sign-up check speaks through Cognito as "PreSignUp failed with error <message>."
export const triggerMessage = (message: string) =>
  message.replace(/^PreSignUp failed with error\s*/i, '').replace(/\.\.$/, '.');

export async function signUp(email: string, password: string, name: string) {
  try {
    await cognito.send(new SignUpCommand({
      ClientId: clientId,
      Username: email,
      Password: password,
      UserAttributes: [{ Name: 'email', Value: email }, ...(name ? [{ Name: 'name', Value: name }] : [])],
    }));
  } catch (err) {
    throw new Error(triggerMessage((err as Error).message));
  }
}

export async function confirmSignUp(email: string, code: string) {
  await cognito.send(new ConfirmSignUpCommand({ ClientId: clientId, Username: email, ConfirmationCode: code }));
}

export async function resendConfirmation(email: string) {
  await cognito.send(new ResendConfirmationCodeCommand({ ClientId: clientId, Username: email }));
}

// Email password resets go through the Lyrcloud pool: its bridge knows Lyr and Lyrshop accounts too (bringing one
// over on a reset), so whichever Lyr app the email signed up on, one code arrives. Afterwards signIn() reaches the
// same wallet through its Lyrcloud fallback. (The Lyr pool answers "code sent" even for emails it doesn't know.)
export async function forgotPassword(email: string) {
  await lyrcloud.send(new ForgotPasswordCommand({ ClientId: lyrcloudClientId, Username: email.trim().toLowerCase() }));
}

export async function confirmForgotPassword(email: string, code: string, password: string) {
  await lyrcloud.send(new ConfirmForgotPasswordCommand({
    ClientId: lyrcloudClientId, Username: email.trim().toLowerCase(), ConfirmationCode: code, Password: password,
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

// Inside the Android app Google refuses sign-in within the WebView (403 disallowed_useragent), so the Hosted UI opens
// in a Chrome Custom Tab and returns to the app's own scheme; MainActivity loads /auth/callback in the WebView, where
// the PKCE verifier is waiting. The token exchange must name the same redirect, so it's remembered with the verifier.
const APP_REDIRECT = 'com.lyrpay.app://auth/callback';
const APP_REDIRECT_KEY = 'lyrpay_app_redirect';
type CapacitorBrowser = { open(options: { url: string }): Promise<void> };
const nativeBrowser = (): CapacitorBrowser | null => {
  const cap = (window as unknown as { Capacitor?: { isNativePlatform?: () => boolean; Plugins?: { Browser?: CapacitorBrowser } } }).Capacitor;
  return cap?.isNativePlatform?.() ? cap.Plugins?.Browser ?? null : null;
};
export const inApp = () => /LyrpayApp/.test(navigator.userAgent);

const redirectUri = () => (sessionStorage.getItem(APP_REDIRECT_KEY) ? APP_REDIRECT : `${location.origin}/auth/callback`);

export async function signInWithGoogle() {
  const browser = nativeBrowser();
  if (browser) sessionStorage.setItem(APP_REDIRECT_KEY, '1');
  else sessionStorage.removeItem(APP_REDIRECT_KEY);
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
  const url = `https://${hostedUiDomain}/oauth2/authorize?${params}`;
  if (browser) await browser.open({ url });
  else location.href = url;
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
  sessionStorage.removeItem(APP_REDIRECT_KEY);
  if (!response.ok) throw new Error(body.error_description || body.error || 'Google sign-in failed.');
  saveSession({ AccessToken: body.access_token, IdToken: body.id_token, RefreshToken: body.refresh_token, ExpiresIn: body.expires_in });
}
