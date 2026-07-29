import React, { useState, useEffect, useRef } from 'react';
import { subscribeWorkers } from '../services/workerPool';
import { bnbChannelManager } from '../services/BnbChannelManager';
import './WorkerPanel.css';

const WorkerPanel = () => {
    const [workers, setWorkers] = useState([]);
    const [channels, setChannels] = useState([]);
    const [now, setNow] = useState(Date.now());
    const timerRef = useRef(null);

    useEffect(() => {
        // Subscribe to active tasks
        const unsubWorkers = subscribeWorkers(setWorkers);
        // Subscribe to channel states
        const unsubChannels = bnbChannelManager.subscribe(setChannels);

        return () => {
            unsubWorkers();
            unsubChannels();
        };
    }, []);

    // Tick every 100ms for elapsed time display while workers are active
    useEffect(() => {
        if (workers.some(w => w.status === 'running' || w.status === 'starting')) {
            timerRef.current = setInterval(() => setNow(Date.now()), 100);
        } else {
            clearInterval(timerRef.current);
        }
        return () => clearInterval(timerRef.current);
    }, [workers]);

    const formatElapsed = (startedAt, finishedAt) => {
        const end = finishedAt || now;
        const ms = end - startedAt;
        if (ms < 1000) return `${ms}ms`;
        return `${(ms / 1000).toFixed(1)}s`;
    };

    // Find active task for a channel
    const getTaskForChannel = (address) => {
        return workers.find(w => w.wallet === address && (w.status === 'running' || w.status === 'starting'));
    };

    // Also track recently completed tasks for visual feedback
    const getCompletedTaskForChannel = (address) => {
        return workers.find(w => w.wallet === address && (w.status === 'done' || w.status === 'failed'));
    };

    return (
        <div className="worker-panel">
            <div className="worker-panel-header">
                <span>Worker Wallets</span>
                <span className="worker-count">{channels.length}</span>
            </div>

            <div className="worker-list">
                {channels.map((ch, idx) => {
                    const task = getTaskForChannel(ch.address) || getCompletedTaskForChannel(ch.address);
                    const isBusy = !!task && (task.status === 'running' || task.status === 'starting');

                    return (
                        <div className={`worker-item ${isBusy ? 'running' : 'idle'} ${task?.status === 'done' ? 'done' : ''} ${task?.status === 'failed' ? 'failed' : ''}`} key={ch.address}>
                            <div className="worker-dot" />
                            <div className="worker-info">
                                <div className="worker-meta-top">
                                    <span className="worker-wallet-id">Worker #{idx + 1}</span>
                                    <span className="worker-wallet-addr" title={ch.address}>
                                        {ch.address.slice(0, 6)}…{ch.address.slice(-4)}
                                    </span>
                                </div>

                                {task ? (
                                    <div className="worker-task-info">
                                        <span className="worker-tool-name">{task.toolName}</span>
                                        <span className="worker-phase">{task.phase}</span>
                                    </div>
                                ) : (
                                    <div className="worker-balances">
                                        <span>{parseFloat(ch.balance?.bnb || 0).toFixed(5)} ETH</span>
                                        <span>•</span>
                                        <span>{parseFloat(ch.balance?.token || 0).toFixed(4)} USDC</span>
                                    </div>
                                )}
                            </div>

                            {task && (
                                <div className="worker-elapsed">
                                    {formatElapsed(task.startedAt, task.finishedAt)}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
};

export default WorkerPanel;
