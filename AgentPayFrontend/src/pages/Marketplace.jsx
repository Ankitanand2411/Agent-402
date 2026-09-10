import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import Button from '../components/Button';
import { fetchTools, extractCategories } from '../services/marketplaceService';
import { formatSFUEL } from '../utils/payment';
import './Marketplace.css';

const Marketplace = () => {
    const navigate = useNavigate();
    const [selectedCategory, setSelectedCategory] = useState('All');
    const [searchQuery, setSearchQuery] = useState('');
    const [selectedAgent, setSelectedAgent] = useState(null);

    // Backend data state
    const [agents, setAgents] = useState([]);
    const [categories, setCategories] = useState(['All']);
    const [isLoading, setIsLoading] = useState(true);
    const [, setError] = useState(null); // kept for a future error banner; not rendered yet

    // Fetch tools from backend on component mount
    useEffect(() => {
        const loadTools = async () => {
            try {
                setIsLoading(true);
                setError(null);

                const tools = await fetchTools();

                if (tools && tools.length > 0) {
                    setAgents(tools);
                    setCategories(extractCategories(tools));
                    console.log('Successfully loaded tools from backend:', tools.length);
                } else {
                    console.warn('No tools returned from backend');
                    setError('No tools available');
                }
            } catch (err) {
                console.error('Failed to fetch tools from backend:', err);
                setError(err.message);
                setAgents([]);
                setCategories(['All']);
            } finally {
                setIsLoading(false);
            }
        };

        loadTools();
    }, []);

    const filteredAgents = agents.filter(agent => {
        const matchesCategory = selectedCategory === 'All' || agent.category === selectedCategory;
        const matchesSearch = agent.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
            agent.description.toLowerCase().includes(searchQuery.toLowerCase());
        return matchesCategory && matchesSearch;
    });

    const handleUseAgent = (agent) => {
        navigate('/agent', { state: { agent } });
    };

    return (
        <div className="marketplace">
            <div className="marketplace-content container">
                <div className="marketplace-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2rem' }}>
                    <div>
                        <h1>AI Tool Marketplace</h1>
                        <p>Discover and integrate autonomous agent capabilities</p>
                    </div>
                    <Button onClick={() => navigate('/add-tool')} variant="primary">
                        + Register New Tool
                    </Button>
                </div>

                {/* Search Bar */}
                <div className="search-container">
                    <svg className="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <circle cx="11" cy="11" r="8" />
                        <path d="m21 21-4.35-4.35" />
                    </svg>
                    <input
                        type="text"
                        className="search-input"
                        placeholder="Search agents..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                    />
                </div>
                {/* Category Pills */}
                <div className="category-section">
                    <div className="category-header">
                        <h3>Browse Apps by Categories</h3>
                        <button className="show-all-btn">Show All</button>
                    </div>
                    <div className="category-pills">
                        {categories.map(category => (
                            <button
                                key={category}
                                className={`category-pill ${selectedCategory === category ? 'active' : ''}`}
                                onClick={() => setSelectedCategory(category)}
                            >
                                {category}
                                <span className="category-count">
                                    {category === 'All'
                                        ? agents.length
                                        : agents.filter(a => a.category === category).length}
                                </span>
                            </button>
                        ))}
                    </div>
                </div>

                {/* Results Count */}
                <div className="results-info">
                    <p>Showing {filteredAgents.length} agents</p>
                </div>

                {/* Agent Grid */}
                <div className="agents-grid">
                    {isLoading ? (
                        <div className="loading-state">
                            <p>Loading tools...</p>
                        </div>
                    ) : filteredAgents.length === 0 ? (
                        <div className="empty-state">
                            <p>No agents found</p>
                        </div>
                    ) : (
                        filteredAgents.map(agent => (
                            <div
                                key={agent.id}
                                className="agent-card"
                                onClick={() => setSelectedAgent(agent)}
                            >
                                <div className="agent-card-content">
                                    <h3 className="agent-card-title">{agent.name}</h3>
                                    <p className="agent-card-description">{agent.description}</p>
                                    <div className="agent-card-footer">
                                        <span className="agent-category-tag">{agent.category}</span>
                                    </div>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </div>

            {/* Agent Detail Modal */}
            {selectedAgent && (
                <div className="modal-overlay" onClick={() => setSelectedAgent(null)}>
                    <div className="modal-content" onClick={(e) => e.stopPropagation()}>
                        <button className="modal-close" onClick={() => setSelectedAgent(null)}>
                            ×
                        </button>

                        <div className="modal-header">
                            <div className="modal-icon-wrapper">
                                <div className="modal-icon">{selectedAgent.icon}</div>
                            </div>
                            <div>
                                <h2>{selectedAgent.name}</h2>
                                <p className="modal-category">{selectedAgent.category}</p>
                            </div>
                        </div>

                        <div className="modal-body">
                            <section className="modal-section">
                                <h3>Description</h3>
                                <p>{selectedAgent.longDescription}</p>
                            </section>

                            <section className="modal-section">
                                <h3>Pricing</h3>
                                <div className="pricing-info">
                                    <div className="price-item">
                                        <span className="price-label">Price per call:</span>
                                        <span className="price-amount">{formatSFUEL(selectedAgent.price)}</span>
                                    </div>
                                    <div className="price-item">
                                        <span className="price-label">Payment method:</span>
                                        <span className="payment-method">Ethereum Sepolia (USDC)</span>
                                    </div>
                                </div>
                            </section>

                            <section className="modal-section">
                                <h3>Sample Usage</h3>
                                <div className="sample-box">
                                    <div className="sample-input">
                                        <strong>Input:</strong> {selectedAgent.sampleInput}
                                    </div>
                                    <div className="sample-output">
                                        <strong>Output:</strong>
                                        <pre>{selectedAgent.sampleOutput}</pre>
                                    </div>
                                </div>
                            </section>

                            <section className="modal-section">
                                <h3>Stats</h3>
                                <div className="stats-grid">
                                    <div className="stat-item">
                                        <span className="stat-label">Rating</span>
                                        <span className="stat-value">{selectedAgent.rating}/5.0</span>
                                    </div>
                                    <div className="stat-item">
                                        <span className="stat-label">Executions</span>
                                        <span className="stat-value">{selectedAgent.executions.toLocaleString()}</span>
                                    </div>
                                    <div className="stat-item">
                                        <span className="stat-label">Creator</span>
                                        <span className="stat-value">{selectedAgent.creator}</span>
                                    </div>
                                </div>
                            </section>
                        </div>

                        <div className="modal-footer">
                            <Button
                                variant="secondary"
                                onClick={() => setSelectedAgent(null)}
                            >
                                Close
                            </Button>
                            <Button
                                variant="primary"
                                onClick={() => handleUseAgent(selectedAgent)}
                            >
                                Use Agent
                            </Button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
};

export default Marketplace;
