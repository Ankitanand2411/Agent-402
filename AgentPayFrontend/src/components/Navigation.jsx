import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import './Navigation.css';

const Navigation = () => {
    const location = useLocation();

    const isActive = (path) => {
        return location.pathname === path;
    };

    return (
        <nav className="navigation">
            <div className="nav-container">
                <Link to="/" className="nav-logo">
                    <img src="/agentpay-logo.png" alt="AgentPay Logo" className="logo-icon" />
                    <span className="logo-text">Agent</span>
                    <span className="logo-accent">402</span>
                </Link>

                <div className="nav-links">
                    <Link to="/" className={`nav-link ${isActive('/') ? 'active' : ''}`}>
                        Home
                    </Link>
                    <Link to="/agent" className={`nav-link ${isActive('/agent') ? 'active' : ''}`}>
                        Agent Interface
                    </Link>
                    <Link to="/marketplace" className={`nav-link ${isActive('/marketplace') ? 'active' : ''}`}>
                        Marketplace
                    </Link>
                    <Link to="/add-tool" className={`nav-link ${isActive('/add-tool') ? 'active' : ''}`}>
                        Add Tool
                    </Link>
                </div>
            </div>
        </nav>
    );
};

export default Navigation;
