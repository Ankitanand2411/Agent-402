import React, { useState } from 'react';
import './ProcessingMessage.css';

const ProcessingMessage = ({ status }) => {
    const [isExpanded, setIsExpanded] = useState(false);

    if (!status) return null;



    const { message, type, args, toolName, amount } = status;

    return (
        <div className="processing-message-container">
            <div className={`processing-message-card status-${type}`}>
                <div className="processing-header">
                    <div className="processing-icon-container">
                        {type === 'info' && <div className="spinner" />}
                        {type === 'payment' && (
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="20" height="20">
                                <rect x="2" y="5" width="20" height="14" rx="2" />
                                <line x1="2" y1="10" x2="22" y2="10" />
                            </svg>
                        )}
                        {type === 'success' && (
                            <svg viewBox="0 0 24 24" fill="none" stroke="#4caf50" strokeWidth="2" width="20" height="20">
                                <polyline points="20 6 9 17 4 12" />
                            </svg>
                        )}
                        {type === 'error' && (
                            <svg viewBox="0 0 24 24" fill="none" stroke="#f44336" strokeWidth="2" width="20" height="20">
                                <circle cx="12" cy="12" r="10" />
                                <line x1="12" y1="8" x2="12" y2="12" />
                                <line x1="12" y1="16" x2="12.01" y2="16" />
                            </svg>
                        )}
                    </div>
                    <span className="processing-text">{message}</span>

                    {(args || toolName || amount) && (
                        <button
                            className="details-toggle"
                            onClick={() => setIsExpanded(!isExpanded)}
                        >
                            {isExpanded ? 'Hide Details' : 'View Details'}
                            <svg
                                viewBox="0 0 24 24"
                                width="12"
                                height="12"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2"
                                style={{ transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}
                            >
                                <path d="M6 9l6 6 6-6" />
                            </svg>
                        </button>
                    )}
                </div>

                {isExpanded && (args || toolName || amount) && (
                    <div className="processing-details">
                        {toolName && <div><strong>Tool:</strong> {toolName}</div>}
                        {amount && <div><strong>Cost:</strong> {amount} USDC</div>}
                        {args && (
                            <div>
                                <strong>Arguments:</strong>
                                <div className="details-json">
                                    {JSON.stringify(args, null, 2)}
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </div>
        </div>
    );
};

export default ProcessingMessage;
