import React from 'react';
import { useNavigate } from 'react-router-dom';
import Button from '../components/Button';
import './Landing.css';

const Landing = () => {
    const navigate = useNavigate();

    return (
        <div className="landing">
            {/* Hero Section */}
            <section className="hero">
                <div className="container">
                    <h1 className="hero-title">
                        MONETIZE AI AGENTS<br />
                        WITH INTERNET-NATIVE<br />
                        PAYMENTS
                    </h1>
                    <p className="hero-subtitle">
                        The open standard for machine-to-machine commerce. <br />
                        Powered by Ethereum Sepolia Testnet and the Yellow Network.
                    </p>
                    <div className="hero-ctas">
                        <Button
                            className="btn-primary-glow"
                            variant="primary"
                            size="lg"
                            onClick={() => navigate('/agent')}
                        >
                            Launch Agent 402
                        </Button>
                        <Button
                            variant="secondary"
                            size="lg"
                            onClick={() => navigate('/marketplace')}
                        >
                            View Marketplace
                        </Button>
                    </div>
                </div>
            </section>

            {/* SECTION 1: AP2 STANDARD (The Protocol) */}
            <section className="protocol-section">
                <div className="container">
                    <span className="section-label">THE STANDARD</span>
                    <h2 className="section-title">x402 PROTOCOL FLOW</h2>
                    <p className="section-subtitle">
                        A standardized HTTP 402 implementation for agent-to-agent transactions.
                    </p>

                    <div className="step-list">
                        <div className="step-item">
                            <div className="step-number">01</div>
                            <div className="step-title">INTENT</div>
                            <div className="step-desc">
                                User discovers tool via AI Agent Registry. <br />
                                Agent negotiates pricing via HTTP 402 negotiation headers.
                            </div>
                        </div>
                        <div className="step-item">
                            <div className="step-number">02</div>
                            <div className="step-title">AUTH</div>
                            <div className="step-desc">
                                Agent signs a cryptographic mandate (DID) authorizing payment. <br />
                                No API keys. Payment IS validation.
                            </div>
                        </div>
                        <div className="step-item">
                            <div className="step-number">03</div>
                            <div className="step-title">SETTLE</div>
                            <div className="step-desc">
                                Instant settlement on Ethereum Sepolia Testnet. <br />
                                Token payments secured by a custom escrow contract.
                            </div>
                        </div>
                        <div className="step-item">
                            <div className="step-number">04</div>
                            <div className="step-title">DELIVER</div>
                            <div className="step-desc">
                                Tool executes and returns data payload with cryptographic proof of payment.
                            </div>
                        </div>
                    </div>
                </div>
            </section>

            {/* SECTION 2: BITE v2 ENGINE (The Escrow Logic) */}
            <section className="protocol-section" style={{ background: '#000', color: '#fff' }}>
                <div className="container">
                    <span className="section-label" style={{ color: '#ccc' }}>THE ENGINE</span>
                    <h2 className="section-title" style={{ color: '#fff' }}>CUSTOM ESCROW</h2>
                    <p className="section-subtitle" style={{ color: '#ccc', borderLeftColor: '#fff' }}>
                        Trustless orchestration engine for conditional payments and parallel execution.
                    </p>

                    <div className="swiss-grid">
                        <div className="grid-cell dark">
                            <h3 className="cell-title">ESCROW LOCK</h3>
                            <p className="cell-desc">
                                Funds are programmatically locked in a smart contract, not sent directly to the vendor.
                                Solves the "delivery risk" problem in autonomous systems.
                            </p>
                        </div>
                        <div className="grid-cell dark">
                            <h3 className="cell-title">ON-CHAIN PROOF</h3>
                            <p className="cell-desc">
                                A cryptographic receipt (Proof-of-Escrow) is generated, serving as a bonded guarantee
                                for the tool provider to begin work.
                            </p>
                        </div>
                        <div className="grid-cell dark">
                            <h3 className="cell-title">PARALLEL EXEC</h3>
                            <p className="cell-desc">
                                Since payments are non-blocking async events, a single agent can trigger
                                100+ tool executions simultaneously without sequence errors.
                            </p>
                        </div>
                        <div className="grid-cell dark">
                            <h3 className="cell-title">CONDITIONAL RELEASE</h3>
                            <p className="cell-desc">
                                Funds are only released when the tool provides a valid result.
                                Failures trigger an instant programmatic refund to the agent.
                            </p>
                        </div>
                    </div>
                </div>
            </section>

            {/* SECTION 3: USE CASES (Grid) */}
            <section className="protocol-section">
                <div className="container">
                    <span className="section-label">UTILITY</span>
                    <h2 className="section-title">USE CASES</h2>

                    <div className="swiss-grid col-2">
                        {/* CSS grid defaults to 2 cols, can make 4 with helper class if added */}
                        <div className="grid-cell">
                            <h3 className="cell-title">API MONETIZATION</h3>
                            <p className="cell-desc">Zero-friction revenue. No user accounts required.</p>
                        </div>
                        <div className="grid-cell">
                            <h3 className="cell-title">AGENT SWARMS</h3>
                            <p className="cell-desc">Orchestrate multi-agent systems with shared bankrolls.</p>
                        </div>
                        <div className="grid-cell">
                            <h3 className="cell-title">PAY-PER-PROMPT</h3>
                            <p className="cell-desc">Access LLMs without monthly subscriptions.</p>
                        </div>
                        <div className="grid-cell">
                            <h3 className="cell-title">ZERO-RISK TRIALS</h3>
                            <p className="cell-desc">Trustless escrow ensures fair play for new users.</p>
                        </div>
                    </div>
                </div>
            </section>

            {/* Footer */}
            <footer className="footer">
                <div className="container">
                    <div className="footer-content">
                        <div className="footer-brand">
                            <div className="footer-logo">
                                AGENT PAY
                            </div>
                            <p style={{ maxWidth: '300px', lineHeight: '1.6' }}>
                                The financial layer for the autonomous agent economy. Built on Ethereum Sepolia &amp; Yellow Network.
                            </p>
                        </div>

                        <div className="footer-links">
                            <div className="footer-column">
                                <h4>PRODUCT</h4>
                                <a href="/agent">Agent Interface</a>
                                <a href="/marketplace">Marketplace</a>
                            </div>

                            <div className="footer-column">
                                <h4>RESOURCES</h4>
                                <a href="https://sepolia.etherscan.io" target="_blank" rel="noopener noreferrer">Sepolia Explorer</a>
                                <a href="https://x402.org" target="_blank" rel="noopener noreferrer">x402 Protocol</a>
                            </div>

                        </div>
                    </div>

                    <div className="footer-bottom">
                        <p>
                            POWERED BY <span className="stellar-badge">SEPOLIA</span> <span className="x402-badge">x402</span>
                        </p>
                        <div style={{ marginTop: '16px' }}>
                            © 2026 AGENT402. PAY PER USE AGENT ECONOMY
                        </div>
                    </div>
                </div>
            </footer>
        </div>
    );
};

export default Landing;
