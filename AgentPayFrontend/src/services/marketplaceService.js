import envConfig from '../config/env';

const REQUEST_TIMEOUT = 30000; // 30 seconds

/**
 * Fetch all tools from the marketplace backend
 * @returns {Promise<Array>} Array of tool objects
 */
export const fetchTools = async () => {
    try {
        const response = await fetch(`${envConfig.MARKETPLACE_URL}/tools`, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
            },
            signal: AbortSignal.timeout(REQUEST_TIMEOUT),
        });

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();

        // Transform backend data to match frontend agent structure
        return transformToolsToAgents(data);
    } catch (error) {
        console.error('Error fetching tools from marketplace:', error);
        throw error;
    }
};


const transformToolsToAgents = (tools) => {
    if (!Array.isArray(tools)) {
        console.warn('Expected tools to be an array, got:', typeof tools);
        return [];
    }

    return tools.map(transformToolToAgent);
};


const transformToolToAgent = (tool) => {
    // Use explicit price field from backend if available, otherwise try to extract or default
    let extractedPrice = 0.00005;
    if (tool.price !== undefined && tool.price !== null) {
        extractedPrice = parseFloat(tool.price);
    } else {
        // Fallback: Extract price from description (e.g., "COSTS: 4 XLM per call")
        const priceMatch = tool.description?.match(/COSTS?:\s*(\d+(?:\.\d+)?)\s*XLM/i);
        extractedPrice = priceMatch ? parseFloat(priceMatch[1]) : 0.00005;
    }

    // Determine category from tool name or description
    const category = determineCategoryFromTool(tool);

    // Generate display name (capitalize and format)
    const displayName = formatToolName(tool.name);

    // Extract sample input/output from parameters if available
    const sampleData = generateSampleFromParameters(tool.parameters);

    return {
        id: tool.name || tool.id || `tool-${Date.now()}`,
        name: displayName,
        category: category,
        description: tool.description || 'No description available',
        longDescription: tool.description || 'No detailed description available',
        price: extractedPrice,
        rating: tool.rating || 4.5,
        verified: tool.verified !== undefined ? tool.verified : true, // Backend tools are verified by default
        icon: tool.icon || getIconForCategory(category),
        sampleInput: sampleData.input,
        sampleOutput: sampleData.output,
        creator: tool.creator || tool.author || 'Marketplace',
        executions: tool.executions || tool.usage_count || Math.floor(Math.random() * 10000),
        // Store original tool data for payment processing
        toolName: tool.name,
        parameters: tool.parameters,
    };
};

/**
 * Determine category from tool name and description
 */
const determineCategoryFromTool = (tool) => {
    const name = (tool.name || '').toLowerCase();
    const desc = (tool.description || '').toLowerCase();

    if (name.includes('weather') || desc.includes('weather')) return 'Weather';
    if (name.includes('audio') || desc.includes('audio') || desc.includes('speech')) return 'Audio';
    if (name.includes('adzuna') || name.includes('job') || desc.includes('job')) return 'Jobs';
    if (name.includes('code') || desc.includes('code')) return 'Code';
    if (name.includes('data') || desc.includes('data')) return 'Data';

    return 'Tools';
};

/**
 * Format tool name for display
 */
const formatToolName = (name) => {
    if (!name) return 'Unnamed Tool';

    // Convert snake_case to Title Case
    return name
        .split('_')
        .map(word => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' ');
};

/**
 * Generate sample input/output from tool parameters
 */
const generateSampleFromParameters = (parameters) => {
    if (!parameters || !parameters.properties) {
        return {
            input: 'Sample input',
            output: 'Sample output'
        };
    }

    // Generate sample input from required parameters
    const required = parameters.required || [];
    const sampleInputs = [];

    required.forEach(param => {
        const prop = parameters.properties[param];
        if (prop) {
            const example = prop.description || param;
            sampleInputs.push(`${param}: ${example}`);
        }
    });

    return {
        input: sampleInputs.join(', ') || 'Sample input',
        output: 'Result will be returned based on your input'
    };
};

/**
 * Get default icon based on category
 * @param {string} category - Category name
 * @returns {string} Emoji icon
 */
const getIconForCategory = (category) => {
    const icons = {
        'Code': 'CD',
        'Research': 'RS',
        'Creative': 'CR',
        'Data': 'DT',
        'Weather': 'WX',
        'Audio': 'AU',
        'Jobs': 'JB',
        'Tools': 'TL',
    };
    return icons[category] || 'TL';
};

/**
 * Extract unique categories from tools
 * @param {Array} tools - Array of tools
 * @returns {Array} Array of unique categories with 'All' prepended
 */
export const extractCategories = (tools) => {
    const categories = new Set(['All']);
    tools.forEach(tool => {
        if (tool.category) {
            categories.add(tool.category);
        }
    });
    return Array.from(categories);
};
