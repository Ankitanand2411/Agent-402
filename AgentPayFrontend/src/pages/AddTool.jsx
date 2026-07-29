import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import GlassCard from '../components/GlassCard';
import Button from '../components/Button';
import envConfig from '../config/env';
import './AddTool.css';

const AddTool = () => {
    const navigate = useNavigate();
    const [mode, setMode] = useState('proxy'); // 'proxy' or 'code'
    const [formData, setFormData] = useState({
        name: '',
        description: '',
        price: '',
        targetUrl: '',
        code: `export default async function(args) {
    // Your logic here
    // args contains the parameters passed by the agent
    return {
        result: "Success",
        data: { message: "Hello World", input: args }
    };
}`,
        walletAddress: '',
    });
    const [parameters, setParameters] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [success, setSuccess] = useState(false);

    const handleChange = (e) => {
        const { name, value } = e.target;
        setFormData(prev => ({
            ...prev,
            [name]: value
        }));
    };

    const handleParamChange = (index, field, value) => {
        const newParams = [...parameters];
        newParams[index][field] = value;
        setParameters(newParams);
    };

    const addParameter = () => {
        setParameters([...parameters, { name: '', type: 'string', description: '' }]);
    };

    const removeParameter = (index) => {
        const newParams = parameters.filter((_, i) => i !== index);
        setParameters(newParams);
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        setLoading(true);
        setError(null);

        try {
            // Construct JSON Schema from UI builder
            const properties = {};
            const required = [];

            parameters.forEach(p => {
                if (p.name) {
                    properties[p.name] = {
                        type: p.type,
                        description: p.description
                    };
                    required.push(p.name);
                }
            });

            const schema = {
                type: "object",
                properties,
                required: required.length > 0 ? required : undefined
            };

            const payload = {
                name: formData.name,
                description: formData.description,
                price: formData.price,
                imgUrl: formData.imgUrl,
                type: mode,
                // Only send relevant fields based on mode
                targetUrl: mode === 'proxy' ? formData.targetUrl : undefined,
                code: mode === 'code' ? formData.code : undefined,
                parameters: schema,
                walletAddress: formData.walletAddress
            };

            const backendUrl = envConfig.MARKETPLACE_URL || "http://localhost:3000/";
            const response = await fetch(`${backendUrl}/tools/register`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.error || "Failed to register tool");
            }

            setSuccess(true);
            setTimeout(() => {
                navigate('/marketplace');
            }, 2000);

        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="add-tool-page">
            <div className="add-tool-container">
                <GlassCard className="add-tool-card">
                    <h2>Register New Tool</h2>
                    <p className="subtitle">Monetize your code or API on AgentPay</p>

                    {error && <div className="error-message">{error}</div>}
                    {success && <div className="success-message">Tool Registered! Redirecting...</div>}

                    <div className="mode-switch">
                        <button
                            className={`mode-btn ${mode === 'proxy' ? 'active' : ''}`}
                            onClick={() => setMode('proxy')}
                            type="button"
                        >
                            Proxy URL
                        </button>
                        <button
                            className={`mode-btn ${mode === 'code' ? 'active' : ''}`}
                            onClick={() => setMode('code')}
                            type="button"
                        >
                            Custom Code
                        </button>
                    </div>

                    <form onSubmit={handleSubmit}>
                        <div className="form-group">
                            <label>Tool Name (ID)</label>
                            <input
                                type="text"
                                name="name"
                                value={formData.name}
                                onChange={handleChange}
                                placeholder="my_awesome_tool"
                                required
                                disabled={loading}
                            />
                        </div>

                        <div className="form-group">
                            <label>Description</label>
                            <input
                                type="text"
                                name="description"
                                value={formData.description}
                                onChange={handleChange}
                                placeholder="What does it do?"
                                required
                                disabled={loading}
                            />
                        </div>

                        <div className="form-row">
                            <div className="form-group half">
                                <label>Price (USDC)</label>
                                <input
                                    type="number"
                                    name="price"
                                    value={formData.price}
                                    onChange={handleChange}
                                    placeholder="0.5"
                                    step="0.1"
                                    required
                                    disabled={loading}
                                />
                            </div>
                            <div className="form-group half">
                                <label>Wallet Address (Optional)</label>
                                <input
                                    type="text"
                                    name="walletAddress"
                                    value={formData.walletAddress}
                                    onChange={handleChange}
                                    placeholder="G..."
                                    disabled={loading}
                                />
                            </div>
                        </div>

                        {mode === 'proxy' ? (
                            <div className="form-group">
                                <label>Target URL (API Endpoint)</label>
                                <input
                                    type="url"
                                    name="targetUrl"
                                    value={formData.targetUrl}
                                    onChange={handleChange}
                                    placeholder="https://api.myapp.com/v1/endpoint"
                                    required={mode === 'proxy'}
                                    disabled={loading}
                                />
                            </div>
                        ) : (
                            <div className="form-group">
                                <label>JavaScript Code (ES Modules)</label>
                                <textarea
                                    name="code"
                                    value={formData.code}
                                    onChange={handleChange}
                                    rows="12"
                                    className="code-editor"
                                    required={mode === 'code'}
                                    disabled={loading}
                                />
                            </div>
                        )}

                        <div className="params-section">
                            <div className="params-header">
                                <label>Input Parameters</label>
                                <button type="button" className="add-param-btn" onClick={addParameter}>+ Add</button>
                            </div>

                            {parameters.map((param, index) => (
                                <div key={index} className="param-row">
                                    <input
                                        type="text"
                                        placeholder="Name"
                                        value={param.name}
                                        onChange={(e) => handleParamChange(index, 'name', e.target.value)}
                                        required
                                    />
                                    <select
                                        value={param.type}
                                        onChange={(e) => handleParamChange(index, 'type', e.target.value)}
                                    >
                                        <option value="string">String</option>
                                        <option value="number">Number</option>
                                        <option value="boolean">Boolean</option>
                                    </select>
                                    <input
                                        type="text"
                                        placeholder="Description"
                                        value={param.description}
                                        onChange={(e) => handleParamChange(index, 'description', e.target.value)}
                                        className="param-desc"
                                    />
                                    <button type="button" className="remove-param-btn" onClick={() => removeParameter(index)}>×</button>
                                </div>
                            ))}
                            {parameters.length === 0 && <p className="no-params">No parameters defined (tool takes no input)</p>}
                        </div>

                        <Button type="submit" variant="primary" disabled={loading} style={{ marginTop: '1.5rem', width: '100%' }}>
                            {loading ? 'Registering...' : 'Register Tool'}
                        </Button>
                    </form>
                </GlassCard>
            </div>
        </div>
    );
};

export default AddTool;
