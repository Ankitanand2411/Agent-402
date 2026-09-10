export default {
  // Marketplace backend URL
  MARKETPLACE_URL: import.meta.env.VITE_MARKETPLACE_URL || 'http://localhost:3000',
  // 'server' runs the agent loop on the backend (/agent/runs); 'client' keeps the in-browser loop.
  AGENT_MODE: import.meta.env.VITE_AGENT_MODE || 'server',
};
