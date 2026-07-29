import React, { useState, useEffect } from 'react';
import {
    subscribeNitroliteStatus,
    initNitrolite,
    disconnectNitrolite,
    closeNitroliteSession,
    NITROLITE_TOKEN_SYMBOL,
} from '../services/nitroliteService';
import { getAgentWallet } from '../services/agentWallet';
import './YellowChannelPanel.css';

const YellowChannelPanel = () => {
    const [nitroStatus, setNitroStatus] = useState({
        connected: false,
        status: 'disconnected',
        address: null,
        appSessionId: null,
        balance: '0',
        error: null,
        wsUrl: null,
    });
    const [isConnecting, setIsConnecting] = useState(false);

    useEffect(() => {
        // Subscribe to status changes from nitroliteService
        const unsub = subscribeNitroliteStatus((s) => setNitroStatus(s));
        return () => unsub();
    }, []);

    const handleConnect = async () => {
        setIsConnecting(true);
        try {
            const wallet = getAgentWallet();
            const privateKey = wallet.privateKey || localStorage.getItem('agent402_wallet_key');
            const address = wallet.address;

            if (!privateKey) {
                throw new Error('Agent wallet not found. Generate a wallet first.');
            }

            await initNitrolite(privateKey, address);
        } catch (err) {
            console.error('[YellowPanel] Connect error:', err.message);
        } finally {
            setIsConnecting(false);
        }
    };

    const handleDisconnect = async () => {
        await closeNitroliteSession();
        disconnectNitrolite();
    };

    const statusColor = {
        connected: '#00e676',
        connecting: '#ffb300',
        disconnected: '#666',
        error: '#ff1744',
    }[nitroStatus.status] || '#666';

    const statusLabel = {
        connected: 'LIVE',
        connecting: 'CONNECTING',
        disconnected: 'OFFLINE',
        error: 'ERROR',
    }[nitroStatus.status] || 'UNKNOWN';

    const shortSessionId = nitroStatus.appSessionId
        ? `${nitroStatus.appSessionId.slice(0, 6)}...${nitroStatus.appSessionId.slice(-4)}`
        : '—';

    return (
        <div className="yellow-panel">
            {/* Header */}
            <div className="yellow-panel-header">
                <div className="yellow-panel-logo">
                    <span className="yellow-logo-dot" style={{ background: statusColor }} />
                    <span className="yellow-panel-title">YELLOW CHANNEL</span>
                    <span className="yellow-badge">ERC-7824</span>
                </div>
                <div className="yellow-status-pill" style={{ color: statusColor, borderColor: statusColor }}>
                    {statusLabel}
                </div>
            </div>

            {/* Stats */}
            <div className="yellow-stats">
                <div className="yellow-stat">
                    <span className="yellow-stat-label">OFF-CHAIN BAL</span>
                    <span className="yellow-stat-value" style={{ color: parseFloat(nitroStatus.balance) > 0 ? '#00e676' : '#666' }}>
                        {parseFloat(nitroStatus.balance || 0).toFixed(4)} {NITROLITE_TOKEN_SYMBOL}
                    </span>
                </div>
                <div className="yellow-stat">
                    <span className="yellow-stat-label">SESSION</span>
                    <span className="yellow-stat-value yellow-mono">{shortSessionId}</span>
                </div>
            </div>

            {/* Features highlight when connected */}
            {nitroStatus.connected && (
                <div className="yellow-features">
                    <span className="yellow-feature">⚡ Instant</span>
                    <span className="yellow-feature">🔗 Off-chain</span>
                    <span className="yellow-feature">0️⃣ Gas</span>
                </div>
            )}

            {/* Error display */}
            {nitroStatus.error && !nitroStatus.connected && (
                <div className="yellow-error">{nitroStatus.error}</div>
            )}

            {/* ClearNode URL */}
            <div className="yellow-node-url">
                {nitroStatus.wsUrl?.replace(/^wss?:\/\//, '') || 'clearnet.yellow.com/ws'}
            </div>

            {/* Actions */}
            <div className="yellow-actions">
                {!nitroStatus.connected ? (
                    <button
                        className="yellow-btn yellow-btn-connect"
                        onClick={handleConnect}
                        disabled={isConnecting || nitroStatus.status === 'connecting'}
                    >
                        {isConnecting || nitroStatus.status === 'connecting'
                            ? '⏳ Connecting...'
                            : '⚡ Connect Yellow'}
                    </button>
                ) : (
                    <button
                        className="yellow-btn yellow-btn-disconnect"
                        onClick={handleDisconnect}
                    >
                        Disconnect
                    </button>
                )}
            </div>

            {/* Fund hint */}
            {nitroStatus.connected && parseFloat(nitroStatus.balance) === 0 && (
                <div className="yellow-hint">
                    Fund your channel at{' '}
                    <a href="https://apps.yellow.com" target="_blank" rel="noopener noreferrer">
                        apps.yellow.com
                    </a>
                    {' '}to enable off-chain payments.
                </div>
            )}
        </div>
    );
};

export default YellowChannelPanel;
