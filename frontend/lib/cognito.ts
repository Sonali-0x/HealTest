const ACCESS_TOKEN_KEY = "healtest.apiToken";
const REFRESH_TOKEN_KEY = "healtest.cognitoRefresh";
const EXPIRY_KEY = "healtest.cognitoExpiry";
const STATE_KEY = "healtest.cognitoState";
const VERIFIER_KEY = "healtest.cognitoVerifier";

type TokenResponse = {
  access_token: string;
  expires_in: number;
  refresh_token?: string;
  token_type: string;
};

function config(): { domain: string; clientId: string; redirectUri: string } {
  const domain = process.env.NEXT_PUBLIC_COGNITO_DOMAIN?.trim().replace(/\/+$/, "");
  const clientId = process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID?.trim();
  if (!domain || !clientId) {
    throw new Error("Cognito sign-in is not configured for this deployment.");
  }
  const parsedDomain = new URL(domain);
  if (
    parsedDomain.protocol !== "https:" ||
    parsedDomain.username ||
    parsedDomain.password ||
    parsedDomain.pathname !== "/" ||
    parsedDomain.search ||
    parsedDomain.hash
  ) {
    throw new Error("The Cognito domain must be an HTTPS origin.");
  }
  return {
    domain,
    clientId,
    redirectUri: `${window.location.origin}/auth/callback`,
  };
}

export function cognitoConfigured(): boolean {
  return Boolean(
    process.env.NEXT_PUBLIC_COGNITO_DOMAIN?.trim() &&
      process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID?.trim(),
  );
}

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function randomString(): string {
  return base64Url(crypto.getRandomValues(new Uint8Array(32)));
}

export async function beginCognitoLogin(): Promise<void> {
  const { domain, clientId, redirectUri } = config();
  const verifier = randomString();
  const state = randomString();
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  const challenge = base64Url(new Uint8Array(digest));
  window.sessionStorage.setItem(STATE_KEY, state);
  window.sessionStorage.setItem(VERIFIER_KEY, verifier);

  const authorize = new URL(`${domain}/oauth2/authorize`);
  authorize.search = new URLSearchParams({
    client_id: clientId,
    response_type: "code",
    scope: "openid email",
    redirect_uri: redirectUri,
    state,
    code_challenge_method: "S256",
    code_challenge: challenge,
  }).toString();
  window.location.assign(authorize.toString());
}

async function exchangeToken(params: URLSearchParams): Promise<TokenResponse> {
  const { domain } = config();
  const response = await fetch(`${domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: params,
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Cognito token exchange failed (${response.status}).`);
  }
  const payload: unknown = await response.json();
  if (
    typeof payload !== "object" ||
    payload === null ||
    !("access_token" in payload) ||
    typeof payload.access_token !== "string" ||
    !("expires_in" in payload) ||
    typeof payload.expires_in !== "number" ||
    !("token_type" in payload) ||
    payload.token_type !== "Bearer"
  ) {
    throw new Error("Cognito returned an invalid token response.");
  }
  const tokens: TokenResponse = {
    access_token: payload.access_token,
    expires_in: payload.expires_in,
    token_type: payload.token_type,
  };
  if ("refresh_token" in payload && typeof payload.refresh_token === "string") {
    tokens.refresh_token = payload.refresh_token;
  }
  return tokens;
}

function saveTokens(tokens: TokenResponse, keepRefreshToken = true): void {
  window.sessionStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
  window.sessionStorage.setItem(
    EXPIRY_KEY,
    String(Date.now() + tokens.expires_in * 1000),
  );
  if (tokens.refresh_token) {
    window.sessionStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
  } else if (!keepRefreshToken) {
    window.sessionStorage.removeItem(REFRESH_TOKEN_KEY);
  }
}

export async function completeCognitoLogin(url: URL): Promise<void> {
  const { clientId, redirectUri } = config();
  const expectedState = window.sessionStorage.getItem(STATE_KEY);
  const verifier = window.sessionStorage.getItem(VERIFIER_KEY);
  const state = url.searchParams.get("state");
  const code = url.searchParams.get("code");
  if (!expectedState || !verifier || !state || state !== expectedState || !code) {
    throw new Error("Cognito sign-in response could not be verified.");
  }

  const tokens = await exchangeToken(
    new URLSearchParams({
      grant_type: "authorization_code",
      client_id: clientId,
      code,
      redirect_uri: redirectUri,
      code_verifier: verifier,
    }),
  );
  saveTokens(tokens, false);
  window.sessionStorage.removeItem(STATE_KEY);
  window.sessionStorage.removeItem(VERIFIER_KEY);
}

export async function cognitoAccessToken(): Promise<string | null> {
  const token = window.sessionStorage.getItem(ACCESS_TOKEN_KEY);
  const expiry = Number(window.sessionStorage.getItem(EXPIRY_KEY) ?? "0");
  if (token && expiry > Date.now() + 60_000) return token;

  const refreshToken = window.sessionStorage.getItem(REFRESH_TOKEN_KEY);
  if (!refreshToken) {
    clearCognitoSession();
    return null;
  }
  const { clientId } = config();
  const refreshed = await exchangeToken(
    new URLSearchParams({
      grant_type: "refresh_token",
      client_id: clientId,
      refresh_token: refreshToken,
    }),
  );
  saveTokens(refreshed);
  return refreshed.access_token;
}

export function clearCognitoSession(): void {
  window.sessionStorage.removeItem(ACCESS_TOKEN_KEY);
  window.sessionStorage.removeItem(REFRESH_TOKEN_KEY);
  window.sessionStorage.removeItem(EXPIRY_KEY);
  window.sessionStorage.removeItem(STATE_KEY);
  window.sessionStorage.removeItem(VERIFIER_KEY);
}

export function signOutCognito(): void {
  const { domain, clientId } = config();
  clearCognitoSession();
  const logout = new URL(`${domain}/logout`);
  logout.search = new URLSearchParams({
    client_id: clientId,
    logout_uri: window.location.origin,
  }).toString();
  window.location.assign(logout.toString());
}
