/**
 * nitroliteService.js
 *
 * Yellow Network / Nitrolite (ERC-7824) state-channel integration for Agent402.
 * Enables instant off-chain micropayments for AI tool calls via ClearNode WebSocket.
 *
 * Flow:
 *   1. Connect to Yellow ClearNode via WebSocket
 *   2. Authenticate with EIP-191 signature (using agent wallet private key)
 *   3. Open / join an App Session ("agent402-tool-payments")
 *   4. For each tool call: submit off-chain state transfer (instant, no gas)
 *   5. Close session when done
 *
 * Chain context: Ethereum Sepolia Testnet (chain ID 11155111)
 * Payment token: USDC on Sepolia (0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238)
 */

import {
    createECDSAMessageSigner,
    createAuthRequestMessage,
    createAuthVerifyMessageFromChallenge,
    createEIP712AuthMessageSigner,
    createAppSessionMessage,
    createSubmitAppStateMessage,
    createCloseAppSessionMessage,
    createGetLedgerBalancesMessage,
    createPingMessageV2,
} from '@erc7824/nitrolite';
import { createWalletClient, http } from 'viem';
import { generatePrivateKey, privateKeyToAccount } from 'viem/accounts';
import { sepolia } from 'viem/chains';

// ── Constants ──────────────────────────────────────────────────────────────────
const CLEARNODE_URL = 'wss://clearnet.yellow.com/ws';
const CLEARNODE_SANDBOX_URL = 'wss://clearnet-sandbox.yellow.com/ws';

// Use the documented ClearNode by default. Sandbox can be enabled via env when needed.
const WS_URL = import.meta.env.VITE_YELLOW_CLEARNODE_URL || CLEARNODE_URL;

const APP_NAME = 'agent402-tool-payments';
const BNB_CHAIN_ID = 11155111; // Ethereum Sepolia Testnet
const SESSION_STORAGE_KEY = 'agent402_yellow_session_key';

// USDC on Ethereum Sepolia Testnet (Circle official — 6 decimals)
export const NITROLITE_TOKEN = '0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238';
export const NITROLITE_TOKEN_DECIMALS = 6;
export const NITROLITE_TOKEN_SYMBOL = 'USDC';

// ── State ──────────────────────────────────────────────────────────────────────
let wsConnection = null;       // WebSocket instance
let signer = null;             // EIP-191 MessageSigner from Nitrolite
let walletAddress = null;      // hex address of agent wallet
let sessionKeyPrivateKey = null;
let sessionKeyAddress = null;
let appSessionId = null;       // active app session ID
let appSessionCounterparty = null;
let appSessionProof = null;
let channelBalance = '0';      // off-chain USDC balance (human-readable)
let connectionStatus = 'disconnected'; // 'disconnected' | 'connecting' | 'connected' | 'error'
let lastError = null;
let requestIdCounter = 1;
let pendingRequests = new Map(); // requestId → { resolve, reject, timeout }
const statusSubscribers = new Set();

// ── Subscriber system ──────────────────────────────────────────────────────────
export const subscribeNitroliteStatus = (fn) => {
    statusSubscribers.add(fn);
    fn(getNitroliteStatus()); // immediately emit current state
    return () => statusSubscribers.delete(fn);
};

const notifySubscribers = () => {
    const status = getNitroliteStatus();
    statusSubscribers.forEach(fn => fn(status));
};

const getErrorMessage = (error) => error?.message || String(error);

const clearPendingRequests = (reason = 'Nitrolite: connection reset') => {
    pendingRequests.forEach(({ reject, timer }) => {
        clearTimeout(timer);
        reject(new Error(reason));
    });
    pendingRequests.clear();
};

const resetSessionKeyState = () => {
    sessionKeyPrivateKey = null;
    sessionKeyAddress = null;
    try {
        localStorage.removeItem(SESSION_STORAGE_KEY);
    } catch {
        // Ignore storage failures in non-browser contexts.
    }
};

const ensureSessionKey = () => {
    if (sessionKeyPrivateKey && sessionKeyAddress) {
        return { privateKey: sessionKeyPrivateKey, address: sessionKeyAddress };
    }

    let privateKey = null;

    try {
        privateKey = localStorage.getItem(SESSION_STORAGE_KEY);
    } catch {
        privateKey = null;
    }

    if (!privateKey) {
        privateKey = generatePrivateKey();
        try {
            localStorage.setItem(SESSION_STORAGE_KEY, privateKey);
        } catch {
            // Ignore storage failures in non-browser contexts.
        }
    }

    const account = privateKeyToAccount(privateKey);
    sessionKeyPrivateKey = privateKey;
    sessionKeyAddress = account.address;
    return { privateKey, address: account.address };
};

const disposeConnection = (closeSocket = true) => {
    clearPendingRequests();

    if (closeSocket && wsConnection) {
        const ws = wsConnection;
        wsConnection = null;
        ws.onopen = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;

        if (
            ws.readyState === WebSocket.OPEN ||
            ws.readyState === WebSocket.CONNECTING
        ) {
            ws.close();
        }
    } else {
        wsConnection = null;
    }
};

export const getNitroliteStatus = () => ({
    connected: connectionStatus === 'connected',
    status: connectionStatus,
    address: walletAddress,
    appSessionId,
    balance: channelBalance,
    error: lastError,
    wsUrl: WS_URL,
});

// ── WebSocket helpers ──────────────────────────────────────────────────────────
const sendRawMessage = async (message) => {
    if (!wsConnection || wsConnection.readyState !== WebSocket.OPEN) {
        throw new Error('Nitrolite: WebSocket not connected');
    }

    // message is already a JSON string from nitrolite API functions
    const msgStr = typeof message === 'string' ? message : JSON.stringify(message);
    wsConnection.send(msgStr);
};

const sendRequest = (message, timeoutMs = 15000) => {
    return new Promise((resolve, reject) => {
        // Parse to extract request_id
        let parsed;
        try {
            parsed = JSON.parse(typeof message === 'string' ? message : JSON.stringify(message));
        } catch {
            parsed = {};
        }

        const reqIdRaw = parsed?.req?.[0] ?? parsed?.req_id ?? parsed?.request_id;
        const reqId = reqIdRaw !== undefined ? String(reqIdRaw) : String(requestIdCounter++);

        const timer = setTimeout(() => {
            pendingRequests.delete(reqId);
            reject(new Error(`Nitrolite: Request ${reqId} timed out after ${timeoutMs}ms`));
        }, timeoutMs);

        pendingRequests.set(String(reqId), { resolve, reject, timer });
        sendRawMessage(message).catch(err => {
            clearTimeout(timer);
            pendingRequests.delete(String(reqId));
            reject(err);
        });
    });
};

// ── WebSocket message handler ──────────────────────────────────────────────────
const handleMessage = (event) => {
    let parsed;
    try {
        parsed = JSON.parse(event.data);
    } catch {
        console.warn('[Nitrolite] Non-JSON message received:', event.data);
        return;
    }

    console.log('[Nitrolite] ← ', parsed.res ?? parsed);

    // resolve pending request by req_id / request_id
    const reqIdRaw = parsed?.res?.[0] ?? parsed?.req?.[0] ?? parsed?.req_id ?? parsed?.request_id;
    const reqId = reqIdRaw !== undefined ? String(reqIdRaw) : '';

    if (reqId && pendingRequests.has(reqId)) {
        const { resolve, timer } = pendingRequests.get(reqId);
        clearTimeout(timer);
        pendingRequests.delete(reqId);
        resolve(parsed);
        return;
    }

    // Handle server-push events (session updates, balance changes, etc.)
    const method = parsed?.res?.[1] ?? parsed?.method ?? '';
    if (method === 'ledger_balance_update' || method === 'balance_update') {
        const newBalance = parsed?.res?.[2]?.balance ?? channelBalance;
        channelBalance = newBalance;
        notifySubscribers();
    }
};

// ── Auth flow ──────────────────────────────────────────────────────────────────
const performAuth = async (privateKey) => {
    if (!walletAddress) throw new Error('Nitrolite: signer not set');

    console.log('[Nitrolite] Starting auth handshake...');

    const account = privateKeyToAccount(privateKey);
    const walletClient = createWalletClient({
        account,
        chain: sepolia,
        transport: http()
    });
    const sessionKey = ensureSessionKey();

    const expires_at = BigInt(Math.floor(Date.now() / 1000) + 3600);
    const domain = { name: APP_NAME };

    const authReqParams = {
        address: walletAddress,
        session_key: sessionKey.address,
        application: APP_NAME,
        expires_at,
        scope: 'console',
        allowances: [],
    };

    // Step 1: send auth_request
    const authReqMsg = await createAuthRequestMessage(authReqParams);
    const challengeResp = await sendRequest(authReqMsg, 10000);

    console.log('[Nitrolite] Auth challenge received');

    // Step 2: sign challenge and send auth_verify
    const challenge = challengeResp?.res?.[2]?.challenge_message ?? challengeResp?.challenge ?? challengeResp;

    const eip712Signer = createEIP712AuthMessageSigner(
        walletClient,
        {
            scope: authReqParams.scope,
            application: authReqParams.application,
            session_key: authReqParams.session_key,
            expires_at: authReqParams.expires_at,
            allowances: authReqParams.allowances,
        },
        domain
    );

    const authVerifyMsg = await createAuthVerifyMessageFromChallenge(eip712Signer, challenge);
    const verifyResp = await sendRequest(authVerifyMsg, 10000);

    const verifyMethod = verifyResp?.res?.[1] ?? verifyResp?.status ?? '';
    console.log('[Nitrolite] Auth verify response:', verifyMethod);

    if (verifyMethod === 'auth_verify' || verifyMethod === 'auth_verify_response' || verifyResp?.status === 'ok' || verifyResp?.res?.[0] === 'auth_verify_response') {
        signer = createECDSAMessageSigner(sessionKey.privateKey);
        console.log('[Nitrolite] ✅ Authenticated successfully');
        return true;
    }

    throw new Error(`Nitrolite auth failed: ${JSON.stringify(verifyResp)}`);
};

// ── Balance fetch ──────────────────────────────────────────────────────────────
const refreshBalance = async () => {
    if (!signer || !walletAddress || connectionStatus !== 'connected') return;
    try {
        const msg = await createGetLedgerBalancesMessage(signer, walletAddress);
        const resp = await sendRequest(msg, 8000);
        // Response shape: { res: ['get_ledger_balances_response', { balances: [...] }] } (Actually index 1 is method and 2 is data)
        const balancesArr = resp?.res?.[2]?.balances ?? resp?.res?.[1]?.balances ?? resp?.balances ?? [];
        const usdcEntry = balancesArr.find(
            b => b.asset?.toLowerCase() === NITROLITE_TOKEN.toLowerCase() ||
                b.asset?.toLowerCase() === 'usdc'
        );
        if (usdcEntry) {
            // Convert from wei to human readable
            const raw = BigInt(usdcEntry.amount ?? 0);
            channelBalance = (raw > 0n)
                ? (Number(raw) / 10 ** NITROLITE_TOKEN_DECIMALS).toFixed(4)
                : '0.0000';
        } else {
            channelBalance = '0.0000';
        }
        notifySubscribers();
    } catch (err) {
        console.warn('[Nitrolite] Balance refresh failed:', err.message);
    }
};

// ── App Session ────────────────────────────────────────────────────────────────
const ensureAppSession = async (counterpartyAddress, amountAtomic = '0') => {
    if (!counterpartyAddress) {
        throw new Error('Nitrolite: counterparty address is required to create an app session');
    }

    if (
        appSessionId &&
        appSessionCounterparty &&
        appSessionCounterparty.toLowerCase() === counterpartyAddress.toLowerCase()
    ) {
        return appSessionId;
    }

    console.log('[Nitrolite] Creating App Session for', APP_NAME, '...');

    const sessionParams = {
        definition: {
            protocol: 'nitroliterpc',
            participants: [walletAddress, counterpartyAddress],
            weights: [100, 0],
            quorum: 100,
            challenge: 0,
            nonce: Date.now(),
        },
        allocations: [
            {
                participant: walletAddress,
                asset: 'usdc',
                amount: amountAtomic.toString(),
            },
            {
                participant: counterpartyAddress,
                asset: 'usdc',
                amount: '0',
            }
        ],
        appSessionData: JSON.stringify({
            appName: APP_NAME,
            version: '1.0.0',
            chain: 'sepolia-testnet',
            chainId: BNB_CHAIN_ID,
        }),
    };

    try {
        const msg = await createAppSessionMessage(signer, sessionParams);
        const resp = await sendRequest(msg, 15000);
        const sid = resp?.res?.[2]?.app_session_id ?? resp?.res?.[1]?.app_session_id ?? resp?.app_session_id;
        if (!sid) {
            throw new Error(`No session ID in response: ${JSON.stringify(resp)}`);
        }
        appSessionId = sid;
        appSessionCounterparty = counterpartyAddress;
        appSessionProof = {
            createSessionRequest: msg,
            createSessionResponse: JSON.stringify(resp),
            definition: sessionParams.definition,
            allocations: sessionParams.allocations,
            counterparty: counterpartyAddress,
            amountAtomic: amountAtomic.toString(),
        };
        console.log('[Nitrolite] ✅ App Session created:', appSessionId);
        notifySubscribers();
        return appSessionId;
    } catch (err) {
        appSessionId = null;
        appSessionCounterparty = null;
        appSessionProof = null;
        const message = getErrorMessage(err);
        if (message.includes('unsupported protocol') && WS_URL === CLEARNODE_SANDBOX_URL) {
            throw new Error(`Nitrolite app sessions are not supported on ${WS_URL}. Switch to ${CLEARNODE_URL} or set VITE_YELLOW_CLEARNODE_URL to a ClearNode that supports application sessions.`);
        }
        throw err;
    }
};

// ── Main connect function ──────────────────────────────────────────────────────
/**
 * Initialize Nitrolite connection using the agent wallet private key.
 * Call this once at startup. Safe to call multiple times (idempotent).
 *
 * @param {string} privateKey - hex private key from agent wallet
 * @param {string} address    - hex wallet address
 */
export const initNitrolite = async (privateKey, address, options = {}) => {
    const { allowSessionKeyRotation = true } = options;

    if (connectionStatus === 'connected' || connectionStatus === 'connecting') {
        console.log('[Nitrolite] Already connected/connecting');
        return getNitroliteStatus();
    }

    if (!privateKey || !address) {
        console.warn('[Nitrolite] No private key / address provided — skipping init');
        return getNitroliteStatus();
    }

    connectionStatus = 'connecting';
    lastError = null;
    walletAddress = address;
    notifySubscribers();

    try {
        signer = null;
        ensureSessionKey();

        await new Promise((resolve, reject) => {
            const ws = new WebSocket(WS_URL);
            wsConnection = ws;

            const timeout = setTimeout(() => {
                ws.close();
                reject(new Error('WebSocket connection timed out'));
            }, 10000);

            ws.onopen = () => {
                clearTimeout(timeout);
                console.log('[Nitrolite] WebSocket connected to', WS_URL);
                resolve();
            };

            ws.onerror = (err) => {
                clearTimeout(timeout);
                console.error('[Nitrolite] WebSocket error:', err);
                reject(new Error('WebSocket connection failed'));
            };

            ws.onclose = () => {
                if (connectionStatus === 'connected') {
                    connectionStatus = 'disconnected';
                    appSessionId = null;
                    lastError = 'Connection closed by server';
                    notifySubscribers();
                    console.warn('[Nitrolite] Connection closed unexpectedly');
                }
            };

            ws.onmessage = handleMessage;
        });

        // Authenticate
        await performAuth(privateKey);

        connectionStatus = 'connected';
        notifySubscribers();

        // Fetch balance in background. App sessions are created lazily once a counterparty is known.
        refreshBalance().catch(console.warn);

        // Keep-alive ping every 30s
        const pingInterval = setInterval(async () => {
            if (wsConnection?.readyState === WebSocket.OPEN) {
                try {
                    const pingMsg = createPingMessageV2();
                    wsConnection.send(pingMsg);
                } catch { /* socket already closed */ }
            } else {
                clearInterval(pingInterval);
            }
        }, 30000);

        // Refresh balance every 15s
        setInterval(() => {
            refreshBalance().catch(console.warn);
        }, 15000);

        console.log('[Nitrolite] ✅ Fully initialized. Ready for off-chain payments.');
        return getNitroliteStatus();

    } catch (err) {
        const message = getErrorMessage(err);
        if (
            allowSessionKeyRotation &&
            (message.includes('session key already exists') || message.includes('session key is already in use'))
        ) {
            console.warn('[Nitrolite] Stale session key detected. Rotating key and retrying auth once.');
            disposeConnection();
            resetSessionKeyState();
            connectionStatus = 'disconnected';
            notifySubscribers();
            return initNitrolite(privateKey, address, { allowSessionKeyRotation: false });
        }

        connectionStatus = 'error';
        lastError = message;
        disposeConnection();
        notifySubscribers();
        console.error('[Nitrolite] Init failed:', message);
        throw err;
    }
};

// ── Off-chain payment ──────────────────────────────────────────────────────────
/**
 * Pay for a tool call instantly via Yellow off-chain state channel.
 * No gas, no on-chain tx. Settles against the open ledger channel.
 *
 * @param {string} toolName       - name of the tool being paid for
 * @param {number|string} amount  - human-readable LINK amount (e.g. "0.01")
 * @param {string} providerWallet - tool provider's wallet address to pay
 * @returns {object} payment receipt for TaskReport
 */
export const payToolWithNitrolite = async (toolName, amount, providerWallet) => {
    if (connectionStatus !== 'connected') {
        throw new Error('Nitrolite not connected. Cannot process off-chain payment.');
    }

    const amountBigInt = BigInt(Math.floor(parseFloat(amount) * 10 ** NITROLITE_TOKEN_DECIMALS));
    if (amountBigInt <= 0n) throw new Error('Payment amount must be > 0');

    const sid = await ensureAppSession(providerWallet, amountBigInt.toString());

    console.log(`[Nitrolite] Paying ${amount} USDC for ${toolName} → ${providerWallet}`);

    const stateParams = {
        app_session_id: sid,
        protocol: 'nitroliterpc',
        state: {
            version: Date.now(),
            allocations: [
                {
                    participant: walletAddress,
                    asset: 'usdc',
                    amount: '0', // sender sends all to provider
                },
                {
                    participant: providerWallet,
                    asset: 'usdc',
                    amount: amountBigInt.toString(),
                }
            ],
            data: JSON.stringify({ tool: toolName, timestamp: Date.now() }),
        }
    };

    const msg = await createSubmitAppStateMessage(signer, stateParams);
    const resp = await sendRequest(msg, 15000);

    const success =
        (typeof resp?.res?.[1] === 'string' && resp.res[1].includes('submit')) ||
        resp?.status === 'ok' ||
        resp?.success === true;

    if (!success) {
        throw new Error(`Nitrolite state submission failed: ${JSON.stringify(resp)}`);
    }

    // Deduct from local balance display
    const prev = parseFloat(channelBalance) || 0;
    channelBalance = Math.max(0, prev - parseFloat(amount)).toFixed(4);
    notifySubscribers();

    const receipt = {
        protocol: 'nitrolite-erc7824',
        appSessionId: sid,
        toolName,
        amount: amount.toString(),
        amountFormatted: `${amount} USDC`,
        asset: NITROLITE_TOKEN,
        from: walletAddress,
        to: providerWallet,
        chain: 'Ethereum Sepolia Testnet (Yellow off-chain)',
        chainId: BNB_CHAIN_ID,
        timestamp: new Date().toISOString(),
        nitroliteResponse: resp,
        paymentMethod: 'yellow-nitrolite',
        instantSettlement: true,
        proof: {
            wsUrl: WS_URL,
            createSessionRequest: appSessionProof?.createSessionRequest || null,
            createSessionResponse: appSessionProof?.createSessionResponse || null,
            submitStateRequest: msg,
            submitStateResponse: JSON.stringify(resp),
            appDefinition: appSessionProof?.definition || null,
            appAllocations: appSessionProof?.allocations || null,
            state: stateParams.state,
            protocol: stateParams.protocol,
            amountAtomic: amountBigInt.toString(),
            amountDecimal: amount.toString(),
            assetSymbol: NITROLITE_TOKEN_SYMBOL,
            assetType: 'usdc',
            payer: walletAddress,
            provider: providerWallet,
            toolName,
        },
    };

    console.log('[Nitrolite] ✅ Off-chain payment complete for', toolName);
    return receipt;
};

// ── Close session ──────────────────────────────────────────────────────────────
export const closeNitroliteSession = async () => {
    if (!appSessionId || !signer) return;
    try {
        const msg = await createCloseAppSessionMessage(signer, { app_session_id: appSessionId });
        await sendRequest(msg, 8000);
        console.log('[Nitrolite] App session closed');
    } catch (err) {
        console.warn('[Nitrolite] Close session error:', err.message);
    } finally {
        signer = null;
        appSessionId = null;
        appSessionCounterparty = null;
        appSessionProof = null;
        disposeConnection();
        connectionStatus = 'disconnected';
        notifySubscribers();
    }
};

// ── Disconnect ─────────────────────────────────────────────────────────────────
export const disconnectNitrolite = () => {
    signer = null;
    disposeConnection();
    connectionStatus = 'disconnected';
    appSessionId = null;
    appSessionCounterparty = null;
    appSessionProof = null;
    channelBalance = '0';
    notifySubscribers();
};

// ── Check if Nitrolite can pay ─────────────────────────────────────────────────
/**
 * Returns true if Nitrolite is connected and has sufficient off-chain balance.
 * @param {string|number} requiredAmount - human-readable USDC amount
 */
export const canPayWithNitrolite = (requiredAmount) => {
    if (connectionStatus !== 'connected') return false;
    const bal = parseFloat(channelBalance) || 0;
    const req = parseFloat(requiredAmount) || 0;
    return bal >= req;
};
