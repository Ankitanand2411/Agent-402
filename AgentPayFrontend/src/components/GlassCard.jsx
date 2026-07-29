import React from 'react';
import './GlassCard.css';

const GlassCard = ({ children, hover = false, className = '', ...props }) => {
    return (
        <div
            className={`glass-card ${hover ? 'glass-card-hover' : ''} ${className}`}
            {...props}
        >
            {children}
        </div>
    );
};

export default GlassCard;
