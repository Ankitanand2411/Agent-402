import React, { useEffect, useState } from 'react';
import './Notification.css';

const Notification = ({ message, type = 'info', duration = 3000, onClose, subtext, args }) => {
    console.log('Notification rendered:', { message, type, args });
    const [isVisible, setIsVisible] = useState(false);

    useEffect(() => {
        // Small delay to trigger animation
        const timer = setTimeout(() => setIsVisible(true), 10);
        return () => clearTimeout(timer);
    }, []);

    useEffect(() => {
        if (duration > 0) {
            const timer = setTimeout(() => {
                setIsVisible(false);
                // Allow animation to finish before calling onClose
                setTimeout(onClose, 300);
            }, duration);
            return () => clearTimeout(timer);
        }
    }, [duration, onClose]);

    return (
        <div className={`notification notification-${type} ${isVisible ? 'visible' : ''}`}>
            <div className="notification-icon">
                {type === 'success' && (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <polyline points="20 6 9 17 4 12" />
                    </svg>
                )}
                {type === 'error' && (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <circle cx="12" cy="12" r="10" />
                        <line x1="12" y1="8" x2="12" y2="12" />
                        <line x1="12" y1="16" x2="12.01" y2="16" />
                    </svg>
                )}
                {type === 'info' && (
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <circle cx="12" cy="12" r="10" />
                        <line x1="12" y1="16" x2="12" y2="12" />
                        <line x1="12" y1="8" x2="12.01" y2="8" />
                    </svg>
                )}
            </div>
            <div className="notification-content">
                <div className="notification-message">{message}</div>
                {subtext && <div className="notification-subtext">{subtext}</div>}
                {args && (
                    <div className="notification-args" style={{ marginTop: '0.5rem', fontSize: '0.85em', opacity: 0.8, maxHeight: '80px', overflowY: 'auto' }}>
                        <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontFamily: 'monospace', backgroundColor: 'rgba(0,0,0,0.1)', padding: '4px', borderRadius: '4px' }}>
                            {JSON.stringify(args, null, 2)}
                        </pre>
                    </div>
                )}
            </div>
            <button className="notification-close" onClick={() => {
                setIsVisible(false);
                setTimeout(onClose, 300);
            }}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
            </button>
        </div>
    );
};

export default Notification;
