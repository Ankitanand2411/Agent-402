// Payment utility functions

export const formatSFUEL = (amount) => {
    try {
        if (!amount) return "0 USDC";
        // Amount is already in human-readable USDC (e.g. "1" = 1 USDC)
        const num = parseFloat(amount);
        if (isNaN(num)) return `${amount} USDC`;
        // Show up to 4 decimals, trim trailing zeros
        return `${parseFloat(num.toFixed(4))} USDC`;
    } catch (e) {
        console.warn("Error formatting USDC:", e);
        return `${amount} USDC`;
    }
};
