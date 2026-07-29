import React from 'react';
import './StatusPill.css';

const StatusPill = ({ status = 'pending', text, className = '' }) => {
    const statusText = text || status.charAt(0).toUpperCase() + status.slice(1);

    return (
        <span className={`status-pill status-${status} ${className}`}>
            <span className="status-dot"></span>
            {statusText}
        </span>
    );
};

export default StatusPill;
