import React from 'react';
import './ChatSidebar.css';

const ChatSidebar = ({ sessions, currentSessionId, onSwitchSession, onNewSession, onDeleteSession, isOpen, footer }) => {
    return (
        <div className={`chat-sidebar ${!isOpen ? 'collapsed' : ''}`}>
            <div className="sidebar-header">
                <button className="new-chat-btn" onClick={onNewSession}>
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M12 5v14M5 12h14" />
                    </svg>
                    New Chat
                </button>
            </div>

            <div className="sidebar-content">
                {sessions.length === 0 && (
                    <div style={{ textAlign: 'center', padding: '20px', color: '#666', fontSize: '0.9em' }}>
                        No saved chats
                    </div>
                )}

                {sessions.map(session => (
                    <div
                        key={session.id}
                        className={`session-item ${session.id === currentSessionId ? 'active' : ''}`}
                        onClick={() => onSwitchSession(session.id)}
                    >
                        <div className="session-info">
                            <div className="session-title">
                                {session.title || 'New Chat'}
                            </div>
                            <div className="session-date">
                                {new Date(session.updatedAt).toLocaleDateString()}
                            </div>
                        </div>

                        <button
                            className="delete-btn"
                            onClick={(e) => {
                                e.stopPropagation();
                                onDeleteSession(session.id);
                            }}
                            title="Delete chat"
                        >
                            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" />
                            </svg>
                        </button>
                    </div>
                ))}
            </div>

            {footer && (
                <div className="sidebar-footer">
                    {footer}
                </div>
            )}
        </div>
    );
};

export default ChatSidebar;
