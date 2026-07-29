// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

contract AgentPayEscrow {
    IERC20 public immutable token;
    address public owner;
    uint256 public timeoutSeconds;

    struct Payment {
        address payer;
        address toolProvider;
        uint256 amount;
        uint256 depositTime;
        bool released;
        bool refunded;
        bool registered;
    }

    mapping(string => Payment) public payments;

    event DepositRegistered(string indexed txHash, address indexed payer, address indexed toolProvider, uint256 amount);
    event PaymentReleased(string indexed txHash, address indexed toolProvider, uint256 amount);
    event PaymentRefunded(string indexed txHash, address indexed payer, uint256 amount);
    event OwnerChanged(address indexed oldOwner, address indexed newOwner);
    event TimeoutChanged(uint256 oldTimeout, uint256 newTimeout);

    modifier onlyOwner() {
        require(msg.sender == owner, "AgentPayEscrow: caller is not owner");
        _;
    }

    constructor(address _token, address _owner, uint256 _timeoutSeconds) {
        require(_token != address(0), "AgentPayEscrow: zero token address");
        require(_owner != address(0), "AgentPayEscrow: zero owner address");
        require(_timeoutSeconds > 0, "AgentPayEscrow: timeout must be > 0");

        token = IERC20(_token);
        owner = _owner;
        timeoutSeconds = _timeoutSeconds;
    }

    function registerDeposit(
        string calldata txHash,
        address payer,
        address toolProvider,
        uint256 amount
    ) external onlyOwner {
        require(bytes(txHash).length > 0, "AgentPayEscrow: empty txHash");
        require(payer != address(0), "AgentPayEscrow: zero payer");
        require(toolProvider != address(0), "AgentPayEscrow: zero toolProvider");
        require(amount > 0, "AgentPayEscrow: zero amount");
        require(!payments[txHash].registered, "AgentPayEscrow: already registered");

        payments[txHash] = Payment({
            payer: payer,
            toolProvider: toolProvider,
            amount: amount,
            depositTime: block.timestamp,
            released: false,
            refunded: false,
            registered: true
        });

        emit DepositRegistered(txHash, payer, toolProvider, amount);
    }

    function releasePaymentByTxHash(
        string calldata txHash,
        address toolProvider,
        uint256 amount
    ) external onlyOwner {
        require(toolProvider != address(0), "AgentPayEscrow: zero toolProvider");
        require(amount > 0, "AgentPayEscrow: zero amount");

        if (payments[txHash].registered) {
            Payment storage p = payments[txHash];
            require(!p.released, "AgentPayEscrow: already released");
            require(!p.refunded, "AgentPayEscrow: already refunded");
            p.released = true;
        }

        uint256 escrowBalance = token.balanceOf(address(this));
        require(escrowBalance >= amount, "AgentPayEscrow: insufficient contract balance");

        bool success = token.transfer(toolProvider, amount);
        require(success, "AgentPayEscrow: token transfer failed");

        emit PaymentReleased(txHash, toolProvider, amount);
    }

    function refundPayment(
        string calldata txHash,
        address payer,
        uint256 amount
    ) external onlyOwner {
        require(payer != address(0), "AgentPayEscrow: zero payer");
        require(amount > 0, "AgentPayEscrow: zero amount");

        if (payments[txHash].registered) {
            Payment storage p = payments[txHash];
            require(!p.released, "AgentPayEscrow: already released");
            require(!p.refunded, "AgentPayEscrow: already refunded");
            p.refunded = true;
        }

        uint256 escrowBalance = token.balanceOf(address(this));
        require(escrowBalance >= amount, "AgentPayEscrow: insufficient contract balance");

        bool success = token.transfer(payer, amount);
        require(success, "AgentPayEscrow: token transfer failed");

        emit PaymentRefunded(txHash, payer, amount);
    }

    function autoRefundTimedOut(string calldata txHash) external {
        Payment storage p = payments[txHash];
        require(p.registered, "AgentPayEscrow: payment not registered");
        require(!p.released, "AgentPayEscrow: already released");
        require(!p.refunded, "AgentPayEscrow: already refunded");
        require(
            block.timestamp >= p.depositTime + timeoutSeconds,
            "AgentPayEscrow: timeout not reached"
        );

        p.refunded = true;

        bool success = token.transfer(p.payer, p.amount);
        require(success, "AgentPayEscrow: token transfer failed");

        emit PaymentRefunded(txHash, p.payer, p.amount);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "AgentPayEscrow: zero address");
        emit OwnerChanged(owner, newOwner);
        owner = newOwner;
    }

    function setTimeout(uint256 newTimeout) external onlyOwner {
        require(newTimeout > 0, "AgentPayEscrow: timeout must be > 0");
        emit TimeoutChanged(timeoutSeconds, newTimeout);
        timeoutSeconds = newTimeout;
    }

    function emergencyWithdraw(address to) external onlyOwner {
        require(to != address(0), "AgentPayEscrow: zero address");
        uint256 balance = token.balanceOf(address(this));
        require(balance > 0, "AgentPayEscrow: nothing to withdraw");
        bool success = token.transfer(to, balance);
        require(success, "AgentPayEscrow: transfer failed");
    }

    function getPayment(string calldata txHash) external view returns (Payment memory) {
        return payments[txHash];
    }

    function contractBalance() external view returns (uint256) {
        return token.balanceOf(address(this));
    }

    function isTimedOut(string calldata txHash) external view returns (bool) {
        Payment storage p = payments[txHash];
        if (!p.registered) return false;
        return block.timestamp >= p.depositTime + timeoutSeconds;
    }
}
