import React, { useState } from 'react';
import './AP2Receipt.css';

const PHASE_CONFIG = {
    intent: { label: 'Intent', description: '402 Challenge', protocol: 'ap2', icon: '📡' },
    authorization: { label: 'Authorization', description: 'Mandate Signed', protocol: 'ap2', icon: '🔑' },
    settlement: { label: 'Settlement', description: 'USDC → Escrow', protocol: 'bite', icon: '🔒' },
    delivery: { label: 'Delivery', description: 'Result + Release', protocol: 'both', icon: '✅' }
};

const PhaseStatusIcon = ({ status }) => {
    if (status === 'complete') return '✓';
    if (status === 'failed') return '✗';
    if (status === 'skipped') return '—';
    return '·';
};

const ProtocolTag = ({ type }) => {
    if (type === 'ap2') return <span className="proto-tag proto-ap2">x402</span>;
    if (type === 'bite') return <span className="proto-tag proto-bite">Sepolia Escrow</span>;
    if (type === 'both') return (
        <>
            <span className="proto-tag proto-ap2">x402</span>
            <span className="proto-tag proto-bite">Sepolia Escrow</span>
        </>
    );
    return null;
};

const formatTime = (ts) => {
    if (!ts) return '';
    const d = new Date(ts);
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 });
};

const shortenAddr = (addr) => {
    if (!addr || addr.length < 12) return addr || '';
    return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
};

const shortenHash = (hash) => {
    if (!hash || hash.length < 16) return hash || '';
    return `${hash.slice(0, 10)}…${hash.slice(-6)}`;
};

const IntentDetails = ({ phase }) => (
    <div className="ap2-phase-details">
        <div className="detail-item"><span className="detail-key">Tool</span><span className="detail-val">{phase.toolName}</span></div>
        {phase.params && <div className="detail-item"><span className="detail-key">Params</span><span className="detail-val">{JSON.stringify(phase.params)}</span></div>}
        {phase.paymentRequired && phase.challenge && (
            <>
                <div className="detail-item">
                    <span className="detail-key">Pay To</span>
                    <span className="detail-val">
                        {shortenAddr(phase.challenge.payTo)}
                        {phase.challenge.escrow && <span className="inline-badge escrow-inline">ESCROW</span>}
                    </span>
                </div>
                <div className="detail-item"><span className="detail-key">Amount</span><span className="detail-val">{(Number(phase.challenge.amount) / 1e18).toFixed(2)} USDC</span></div>
                {phase.challenge.toolProvider && (
                    <div className="detail-item"><span className="detail-key">Provider</span><span className="detail-val">{shortenAddr(phase.challenge.toolProvider)}</span></div>
                )}
            </>
        )}
        {phase.paymentRequired === false && <div className="detail-item"><span className="detail-key">Payment</span><span className="detail-val free-tag">Not required</span></div>}
    </div>
);

const AuthorizationDetails = ({ phase }) => (
    <div className="ap2-phase-details">
        {phase.mandate && (
            <>
                <div className="detail-item"><span className="detail-key">Signer</span><span className="detail-val">{shortenAddr(phase.mandate.signedBy?.split(':').pop())}</span></div>
                <div className="detail-item"><span className="detail-key">Nonce</span><span className="detail-val">{phase.mandate.nonce}</span></div>
                <div className="detail-item"><span className="detail-key">Valid</span><span className="detail-val">{new Date(phase.mandate.validUntil).toLocaleTimeString()}</span></div>
            </>
        )}
        {phase.signaturePreview && <div className="detail-item"><span className="detail-key">Sig</span><span className="detail-val sig-preview">{phase.signaturePreview}</span></div>}
    </div>
);

const SettlementDetails = ({ phase }) => (
    <div className="ap2-phase-details">
        {phase.txHash && (
            <div className="detail-item">
                <span className="detail-key">Tx</span>
                <span className="detail-val">
                    <a href={phase.explorerUrl} target="_blank" rel="noopener noreferrer">
                        {shortenHash(phase.txHash)} ↗
                    </a>
                </span>
            </div>
        )}
        {phase.from && <div className="detail-item"><span className="detail-key">From</span><span className="detail-val">{shortenAddr(phase.from)}</span></div>}
        {phase.to && (
            <div className="detail-item">
                <span className="detail-key">To</span>
                <span className="detail-val">
                    {shortenAddr(phase.to)}
                    {phase.escrow && <span className="inline-badge escrow-inline">ESCROW</span>}
                </span>
            </div>
        )}
        {phase.amount && <div className="detail-item"><span className="detail-key">Amount</span><span className="detail-val">{(Number(phase.amount) / 1e18).toFixed(2)} USDC</span></div>}
        {phase.blockNumber && <div className="detail-item"><span className="detail-key">Block</span><span className="detail-val">{phase.blockNumber}</span></div>}
        {phase.chain && <div className="detail-item"><span className="detail-key">Chain</span><span className="detail-val">{phase.chain}</span></div>}
        {phase.escrow && (
            <div className="escrow-hold-banner">
                <span className="escrow-hold-icon">🔒</span>
                <span>Funds held in Sepolia Escrow until tool delivers</span>
            </div>
        )}
    </div>
);

const DeliveryDetails = ({ phase, receipt }) => {
    const escrowRelease = receipt?.escrowReceipt?.escrowRelease || receipt?.serverAttestation?.escrowRelease;

    return (
        <div className="ap2-phase-details">
            {phase.httpStatus && <div className="detail-item"><span className="detail-key">HTTP</span><span className="detail-val">{phase.httpStatus}</span></div>}

            {/* BNB Escrow Release Info */}
            {escrowRelease && (
                <div className="escrow-release-section">
                    <div className="escrow-release-header">
                        <span className="proto-tag proto-bite sm">Sepolia Escrow</span>
                        <span className="escrow-release-label">Escrow Resolution</span>
                    </div>

                    <div className={`escrow-release-status status-${escrowRelease.status}`}>
                        {escrowRelease.status === 'released' && (
                            <>
                                <span className="status-icon">✓</span>
                                <span>Released to tool provider</span>
                            </>
                        )}
                        {escrowRelease.status === 'refunded' && (
                            <>
                                <span className="status-icon">↩</span>
                                <span>Refunded to agent</span>
                            </>
                        )}
                        {escrowRelease.status === 'self-settled' && (
                            <>
                                <span className="status-icon">✓</span>
                                <span>Self-settled (escrow = provider)</span>
                            </>
                        )}
                        {escrowRelease.status === 'release-failed' && (
                            <>
                                <span className="status-icon">⚠</span>
                                <span>Release failed</span>
                            </>
                        )}
                        {escrowRelease.status === 'refund-failed' && (
                            <>
                                <span className="status-icon">⚠</span>
                                <span>Refund failed</span>
                            </>
                        )}
                        {escrowRelease.status === 'no-key' && (
                            <>
                                <span className="status-icon">—</span>
                                <span>No escrow key configured</span>
                            </>
                        )}
                    </div>

                    {escrowRelease.releaseTxHash && (
                        <div className="detail-item"><span className="detail-key">Release Tx</span><span className="detail-val">{shortenHash(escrowRelease.releaseTxHash)}</span></div>
                    )}
                    {escrowRelease.releasedTo && (
                        <div className="detail-item"><span className="detail-key">To</span><span className="detail-val">{shortenAddr(escrowRelease.releasedTo)}</span></div>
                    )}
                    {escrowRelease.refundTxHash && (
                        <div className="detail-item"><span className="detail-key">Refund Tx</span><span className="detail-val">{shortenHash(escrowRelease.refundTxHash)}</span></div>
                    )}
                    {escrowRelease.refundedTo && (
                        <div className="detail-item"><span className="detail-key">To</span><span className="detail-val">{shortenAddr(escrowRelease.refundedTo)}</span></div>
                    )}
                </div>
            )}
        </div>
    );
};

const DETAIL_RENDERERS = {
    intent: IntentDetails,
    authorization: AuthorizationDetails,
    settlement: SettlementDetails,
    delivery: DeliveryDetails
};

const AP2Receipt = ({ receipt }) => {
    const [isExpanded, setIsExpanded] = useState(false);
    const [showAudit, setShowAudit] = useState(false);
    const [copied, setCopied] = useState(false);

    if (!receipt) return null;

    const phases = Object.entries(receipt.phases);
    const isEscrow = receipt.protocol === 'x402-bnb-escrow' || receipt.protocol === 'ap2-bite-escrow' || receipt.protocol === 'x402';

    const handleCopy = () => {
        navigator.clipboard.writeText(JSON.stringify(receipt, null, 2));
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div className={`ap2-receipt ${isEscrow ? 'escrow-mode' : ''}`}>
            <div className="ap2-receipt-header" onClick={() => setIsExpanded(!isExpanded)}>
                <div className="ap2-receipt-title">
                    <div className="protocol-badges">
                        <span className="proto-tag proto-ap2">x402</span>
                        {isEscrow && <span className="proto-tag proto-bite">Sepolia Escrow</span>}
                    </div>
                    <span className="receipt-label">Payment Receipt</span>
                    <span className="receipt-id">{receipt.receiptId}</span>
                </div>
                <span className={`ap2-outcome ${receipt.outcome}`}>
                    {receipt.outcome === 'success' ? '✓ Success' : receipt.outcome === 'failed' ? `✗ Failed at ${receipt.failedAt}` : '⏳ Pending'}
                </span>
            </div>

            {isExpanded && (
                <>
                    {/* Protocol Legend Removed */}

                    <div className="ap2-stepper">
                        {phases.map(([phaseName, phase], idx) => {
                            const config = PHASE_CONFIG[phaseName];
                            if (!config) return null;
                            const DetailRenderer = DETAIL_RENDERERS[phaseName];
                            const isLast = idx === phases.length - 1;
                            const isBitePhase = config.protocol === 'bite';
                            const isBothPhase = config.protocol === 'both';

                            return (
                                <div className={`ap2-phase ${isBitePhase ? 'bite-phase' : ''} ${isBothPhase ? 'both-phase' : ''}`} key={phaseName}>
                                    <div className="ap2-phase-indicator">
                                        <div className={`ap2-phase-dot ${phase.status} ${isBitePhase ? 'bite-dot' : ''}`}>
                                            <PhaseStatusIcon status={phase.status} />
                                        </div>
                                        {!isLast && <div className={`ap2-phase-line ${phase.status} ${isBitePhase ? 'bite-line' : ''}`} />}
                                    </div>
                                    <div className="ap2-phase-content">
                                        <div className="ap2-phase-header">
                                            <div className="phase-name-group">
                                                <span className="ap2-phase-name">{config.label}</span>
                                                {isEscrow && <ProtocolTag type={config.protocol} />}
                                            </div>
                                            <span className="ap2-phase-time">{formatTime(phase.timestamp)}</span>
                                        </div>
                                        {phase.status !== 'pending' && phase.status !== 'skipped' && DetailRenderer && (
                                            <DetailRenderer phase={phase} receipt={receipt} />
                                        )}
                                        {phase.status === 'skipped' && (
                                            <div className="ap2-phase-details"><span className="free-tag">Free tool — payment skipped</span></div>
                                        )}
                                        {phase.status === 'failed' && receipt.error && phaseName === receipt.failedAt && (
                                            <div className="ap2-error-msg">{receipt.error}</div>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>

                    <div className="ap2-audit-toggle" onClick={() => setShowAudit(!showAudit)}>
                        {showAudit ? '▲ Hide' : '▼ Show'} Full Audit JSON
                    </div>

                    {showAudit && (
                        <div className="ap2-audit-json">
                            <button className={`ap2-copy-btn ${copied ? 'copied' : ''}`} onClick={handleCopy}>
                                {copied ? 'Copied' : 'Copy'}
                            </button>
                            <pre>{JSON.stringify(receipt, null, 2)}</pre>
                        </div>
                    )}
                </>
            )}
        </div>
    );
};

export default AP2Receipt;
