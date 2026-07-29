import React, { useState } from 'react';
import './TaskReport.css';
import { formatSFUEL } from '../utils/payment';
import AP2Receipt from './AP2Receipt';

const TaskReport = ({ report }) => {
    const [isExpanded, setIsExpanded] = useState(false);

    if (!report) return null;

    const { toolName, args, amount, status, txHash, message } = report;

    return (
        <div className="task-report-container">
            <div className={`task-report-card status-${status}`}>
                <div className="report-header" onClick={() => setIsExpanded(!isExpanded)}>
                    <div className="report-title-section">
                        <div className="report-icon">
                            {status === 'success' ? (
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                                    <polyline points="22 4 12 14.01 9 11.01" />
                                </svg>
                            ) : (
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                    <circle cx="12" cy="12" r="10" />
                                    <line x1="12" y1="8" x2="12" y2="12" />
                                    <line x1="12" y1="16" x2="12.01" y2="16" />
                                </svg>
                            )}
                        </div>
                        <div className="report-summary">
                            <span className="report-tool-name">{toolName || 'Task Execution'}</span>
                            <span className="report-status-text">
                                {report.executions
                                    ? `${report.executions.length} Tool${report.executions.length !== 1 ? 's' : ''} Executed`
                                    : (status === 'success' ? 'Completed Successfully' : 'Failed')}
                            </span>
                        </div>
                    </div>
                    <div className={`dropdown-arrow ${isExpanded ? 'expanded' : ''}`}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M6 9l6 6 6-6" />
                        </svg>
                    </div>
                </div>

                {isExpanded && (
                    <div className="report-details">
                        {/* Grouped Executions View */}
                        {report.executions && report.executions.length > 0 ? (
                            <div className="executions-list">
                                {report.executions.map((exec, index) => (
                                    <div key={index} className="execution-item" style={{
                                        borderBottom: index < report.executions.length - 1 ? '1px solid rgba(255,255,255,0.05)' : 'none',
                                        paddingBottom: '12px',
                                        marginBottom: '12px'
                                    }}>
                                        <div className="detail-row">
                                            <span className="detail-label" style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
                                                {exec.toolName}
                                            </span>
                                            <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                                                {exec.receipt?.paymentMethod === 'yellow-nitrolite' || exec.receipt?.protocol === 'nitrolite-erc7824' ? (
                                                    <span style={{
                                                        fontSize: '9px', padding: '2px 6px', fontWeight: 800,
                                                        background: '#ffe600', color: '#000', border: '1.5px solid #000',
                                                        fontFamily: 'monospace', letterSpacing: '0.04em'
                                                    }}>⚡ YELLOW ERC-7824</span>
                                                ) : exec.txHash ? (
                                                    <span style={{
                                                        fontSize: '9px', padding: '2px 6px', fontWeight: 800,
                                                        background: '#f5f5f5', color: '#555', border: '1.5px solid #ccc',
                                                        fontFamily: 'monospace'
                                                    }}>🔗 SEPOLIA ESCROW</span>
                                                ) : null}
                                                <span className={`status-pill ${exec.status}`} style={{ fontSize: '0.8em', padding: '2px 8px', borderRadius: '4px', background: exec.status === 'success' ? 'rgba(76, 175, 80, 0.1)' : 'rgba(244, 67, 54, 0.1)', color: exec.status === 'success' ? '#4caf50' : '#f44336' }}>
                                                    {exec.status}
                                                </span>
                                            </div>
                                        </div>

                                        <div className="detail-row">
                                            <span className="detail-label">Cost:</span>
                                            <span className="detail-value">
                                                {exec.receipt?.paymentMethod === 'yellow-nitrolite' || exec.receipt?.protocol === 'nitrolite-erc7824'
                                                    ? `${exec.cost || 0} LINK (off-chain ⚡)`
                                                    : exec.cost ? formatSFUEL(exec.cost) : 'Free'}
                                            </span>
                                        </div>

                                        {exec.txHash && (
                                            <div className="detail-row">
                                                <span className="detail-label">Transaction:</span>
                                                <span className="detail-value code-font">
                                                    <a
                                                        href={`https://sepolia.etherscan.io/tx/${exec.txHash}`}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        style={{ color: 'inherit', textDecoration: 'none' }}
                                                    >
                                                        {exec.txHash.substring(0, 16)}...
                                                        <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" style={{ marginLeft: 4 }}>
                                                            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                                                            <polyline points="15 3 21 3 21 9" />
                                                            <line x1="10" y1="14" x2="21" y2="3" />
                                                        </svg>
                                                    </a>
                                                </span>
                                            </div>
                                        )}

                                        {exec.args && (
                                            <div className="detail-section">
                                                <span className="detail-label">Arguments:</span>
                                                <div className="detail-json">
                                                    {JSON.stringify(exec.args, null, 2)}
                                                </div>
                                            </div>
                                        )}

                                        {exec.receipt && (
                                            <AP2Receipt receipt={exec.receipt} />
                                        )}
                                    </div>
                                ))}
                            </div>
                        ) : (
                            /* Single Execution / Legacy View */
                            <>
                                <div className="detail-row">
                                    <span className="detail-label">Cost:</span>
                                    <span className="detail-value">{amount ? formatSFUEL(amount) : 'Free'}</span>
                                </div>

                                {report.reasoning && (
                                    <div className="detail-section">
                                        <span className="detail-label">Task Organization (Gemini Plan):</span>
                                        <div className="detail-text" style={{ whiteSpace: 'pre-wrap', marginBottom: '8px', color: '#bdc3c7' }}>
                                            {report.reasoning}
                                        </div>
                                    </div>
                                )}

                                {txHash && (
                                    <div className="detail-row">
                                        <span className="detail-label">Transaction:</span>
                                        <span className="detail-value code-font">
                                            <a
                                                href={`https://sepolia.etherscan.io/tx/${txHash}`}
                                                target="_blank"
                                                rel="noopener noreferrer"
                                                style={{ color: 'inherit', textDecoration: 'none' }}
                                            >
                                                {txHash.substring(0, 16)}...
                                                <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" style={{ marginLeft: 4 }}>
                                                    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                                                    <polyline points="15 3 21 3 21 9" />
                                                    <line x1="10" y1="14" x2="21" y2="3" />
                                                </svg>
                                            </a>
                                        </span>
                                    </div>
                                )}

                                {args && (
                                    <div className="detail-section">
                                        <span className="detail-label">Task Parameters (Gemini Planned):</span>
                                        <div className="detail-json">
                                            {JSON.stringify(args, null, 2)}
                                        </div>
                                    </div>
                                )}

                                {message && (
                                    <div className="detail-section">
                                        <span className="detail-label">Message:</span>
                                        <p className="detail-text">{message}</p>
                                    </div>
                                )}
                            </>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};

export default TaskReport;
