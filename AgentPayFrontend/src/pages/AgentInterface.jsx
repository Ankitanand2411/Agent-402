import React, { useState, useRef, useEffect } from 'react';
import Button from '../components/Button';
import GlassCard from '../components/GlassCard';
import StatusPill from '../components/StatusPill';
import Notification from '../components/Notification';
import AgentWalletPanel from '../components/AgentWalletPanel';
import WorkerPanel from '../components/WorkerPanel';
import ProcessingMessage from '../components/ProcessingMessage';
import TaskReport from '../components/TaskReport';
import { formatSFUEL } from '../utils/payment';
import {
  fetchMarketplaceTools,
  processQueryWithGemini,
  wakeUpServices
} from '../services/geminiService';
import envConfig from '../config/env';
import './AgentInterface.css';
import ChatSidebar from '../components/ChatSidebar';
import { v4 as uuidv4 } from 'uuid';
import MarkdownRenderer from '../components/MarkdownRenderer';


const AgentInterface = () => {
  const [messages, setMessages] = useState([]);
  const [inputValue, setInputValue] = useState('');
  const [isProcessing, setIsProcessing] = useState(false);
  const [availableTools, setAvailableTools] = useState([]);
  const [isInitialized, setIsInitialized] = useState(false);
  const [expandedResponses, setExpandedResponses] = useState(new Set());
  const [audioBlobs, setAudioBlobs] = useState({});
  const [processingStatus, setProcessingStatus] = useState(null);
  const [notifications, setNotifications] = useState([]);
  const [agentPublicKey, setAgentPublicKey] = useState(null);
  const messagesEndRef = useRef(null);
  const messageIdRef = useRef(1);

  const inputRef = useRef(null);

  useEffect(() => {
    if (!isProcessing && inputRef.current) {
      inputRef.current.focus();
    }
  }, [isProcessing]);

  const [sessions, setSessions] = useState([]);
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, processingStatus]);

  const initializedRef = useRef(false);

  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;

    wakeUpServices();

    const initialize = async () => {
      try {
        const tools = await fetchMarketplaceTools();

        if (tools.length === 0) {
          addMessage('system', 'No tools available from marketplace.');
        } else {
          setAvailableTools(tools);
          setIsInitialized(true);
        }
      } catch (error) {
        addMessage('system', `Initialization failed: ${error.message}`);
      }
    };

    initialize();
  }, []);

  useEffect(() => {
    const savedSessions = localStorage.getItem('chat_sessions');
    if (savedSessions) {
      const parsed = JSON.parse(savedSessions);
      setSessions(parsed);

      if (parsed.length > 0) {
        loadSession(parsed[0].id);
      } else {
        createNewSession();
      }
    } else {
      createNewSession();
    }
  }, []);

  useEffect(() => {
    if (sessions.length > 0) {
      localStorage.setItem('chat_sessions', JSON.stringify(sessions));
    }
  }, [sessions]);

  useEffect(() => {
    if (currentSessionId && messages.length > 0) {
      localStorage.setItem(`chat_session_${currentSessionId}`, JSON.stringify(messages));

      setSessions(prev => {
        const sessionIndex = prev.findIndex(s => s.id === currentSessionId);
        if (sessionIndex >= 0) {
          const session = prev[sessionIndex];
          const firstUserMsg = messages.find(m => m.type === 'user');

          let newTitle = session.title;
          if ((!session.title || session.title === 'New Chat') && firstUserMsg) {
            newTitle = firstUserMsg.content.slice(0, 30) + (firstUserMsg.content.length > 30 ? '...' : '');
          }

          if (newTitle !== session.title || session.updatedAt !== new Date().toISOString()) {
            const newSessions = [...prev];
            newSessions[sessionIndex] = {
              ...session,
              title: newTitle,
              updatedAt: new Date().toISOString()
            };
            newSessions.sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt));
            return newSessions;
          }
        }
        return prev;
      });
    }
  }, [messages, currentSessionId]);

  const loadSession = (sessionId) => {
    const savedMessages = localStorage.getItem(`chat_session_${sessionId}`);
    if (savedMessages) {
      const parsedMsgs = JSON.parse(savedMessages);
      const hydratedMsgs = parsedMsgs.map(m => ({
        ...m,
        timestamp: new Date(m.timestamp)
      }));
      setMessages(hydratedMsgs);
      setCurrentSessionId(sessionId);
    }
  };

  const createNewSession = () => {
    const newId = Date.now().toString();
    const newSession = {
      id: newId,
      title: 'New Chat',
      updatedAt: new Date().toISOString()
    };

    setSessions(prev => [newSession, ...prev]);
    setCurrentSessionId(newId);
    setMessages([{
      type: 'system',
      content: 'Welcome to Agent402 with AI!',
      timestamp: new Date(),
      id: uuidv4()
    }]);
  };

  const deleteSession = (sessionId) => {
    localStorage.removeItem(`chat_session_${sessionId}`);

    const newSessions = sessions.filter(s => s.id !== sessionId);
    setSessions(newSessions);
    localStorage.setItem('chat_sessions', JSON.stringify(newSessions));

    if (sessionId === currentSessionId) {
      if (newSessions.length > 0) {
        loadSession(newSessions[0].id);
      } else {
        createNewSession();
      }
    }
  };

  const handleSwitchSession = (sessionId) => {
    if (sessionId === currentSessionId) return;
    loadSession(sessionId);
  };


  const addMessage = (type, content, extra = {}) => {
    const messageId = uuidv4();
    setMessages(prev => [...prev, {
      type,
      content,
      timestamp: new Date(),
      id: messageId,
      ...extra
    }]);
    return messageId;
  };

  const addNotification = (notif) => {
    const id = Date.now() + Math.random();
    setNotifications(prev => [...prev, { ...notif, id }]);

    if (notif.duration !== 0) {
      setTimeout(() => {
        removeNotification(id);
      }, notif.duration || 5000);
    }
  };

  const removeNotification = (id) => {
    setNotifications(prev => prev.filter(n => n.id !== id));
  };

  const generateAudio = async (text, messageId) => {
    try {
      const backendUrl = envConfig.MARKETPLACE_URL || 'http://localhost:3000/';

      const endpoint = `${backendUrl.replace(/\/tools\/?$/, '')}/tools/get_audio`;
      console.log('Audio endpoint:', endpoint, 'for message:', messageId);

      const response = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ text })
      });

      console.log('Audio response status:', response.status);

      if (!response.ok) {
        throw new Error(`Failed to generate audio: ${response.status} - Check backend URL: ${endpoint}`);
      }

      const responseData = await response.json();
      let audioUrl;

      if (responseData.type === 'audio' && responseData.encoding === 'base64') {
        const binaryString = atob(responseData.data);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
          bytes[i] = binaryString.charCodeAt(i);
        }
        const blob = new Blob([bytes], { type: 'audio/wav' });
        audioUrl = URL.createObjectURL(blob);
      } else {
        throw new Error('Invalid audio response format. Expected x402 JSON with base64.');
      }

      console.log('Audio URL created:', audioUrl);

      setAudioBlobs(prev => ({
        ...prev,
        [messageId]: audioUrl
      }));
    } catch (error) {
      console.error('Error generating audio:', error);
      addMessage('system', `Error generating audio: ${error.message}`);
    }
  };

  const handleSendMessage = async () => {
    if (!inputValue.trim() || isProcessing) return;

    if (!isInitialized) {
      addMessage('system', ' System not initialized. Please wait or refresh the page.');
      return;
    }

    const userQuery = inputValue;
    addMessage('user', userQuery);
    setInputValue('');
    setIsProcessing(true);

    const chatHistory = messages
      .filter(m => m.type === 'user' || m.type === 'agent')
      .map(m => ({
        role: m.type === 'user' ? 'user' : 'model',
        parts: [{ text: m.content }]
      }));

    try {
      const result = await processQueryWithGemini(
        userQuery,
        availableTools,
        (progress) => {
          switch (progress.step) {
            case 'analyzing':
            case 'generating_response':
              setProcessingStatus({
                message: progress.message,
                type: 'info',
                args: progress.args
              });
              break;
            case 'processing_payment':
            case 'awaiting_confirmation':
            case 'delivering':
            case 'authorizing':
              setProcessingStatus(prev => ({
                ...prev,
                message: progress.message,
                type: 'info',
                args: progress.args,
                receipt: progress.receipt
              }));
              break;
            case 'planning_complete':
              addMessage('task-report', 'Planning', {
                report: {
                  toolName: "Planning",
                  status: 'success',
                  reasoning: progress.plan,
                  message: `Plan established. Execution flow: ${progress.toolsParam.length > 1 ? 'Parallel' : 'Sequential'} (${progress.toolsParam.join(', ')})`,
                  toolsParam: progress.toolsParam
                }
              });
              break;
            case 'tool_selected':
              setProcessingStatus(prev => ({
                ...prev,
                message: `Using tool: ${progress.toolName}`,
                type: 'info',
                args: progress.args || prev?.args,
                toolName: progress.toolName || prev?.toolName,
                txHash: prev?.txHash,
                receipt: progress.receipt
              }));
              break;
            case 'payment_required':
              setProcessingStatus(prev => ({
                ...prev,
                message: `Payment Required: ${formatSFUEL(progress.amount)} for ${progress.toolName}`,
                type: 'payment',
                args: progress.args,
                amount: progress.amount,
                toolName: progress.toolName,
                txHash: prev?.txHash,
                receipt: progress.receipt
              }));
              break;
            case 'payment_confirmed':
              setProcessingStatus(prev => ({
                ...prev,
                message: `Payment Confirmed!`,
                type: 'success',
                args: progress.args,
                txHash: progress.txHash,
                receipt: progress.receipt
              }));
              addNotification({
                message: 'Payment Successful',
                subtext: `Amount: ${formatSFUEL(progress.amount)}`,
                type: 'success',
                duration: 5000,
                args: progress.args || processingStatus?.args
              });
              break;
          }
        },
        chatHistory
      );

      await new Promise(resolve => setTimeout(resolve, 500));

      const finalStatusArgs = processingStatus?.args;
      const finalStatusAmount = processingStatus?.amount;
      const finalStatusTp = processingStatus?.type;
      const finalStatusTxHash = processingStatus?.txHash;

      setProcessingStatus(null);

      if (result.success) {
        const audioUrls = result.toolResponses
          ?.filter(r => r.type === 'audio')
          .map(r => {
            if (r.url) return r.url;
            if (r.data && r.encoding === 'base64') {
              try {
                const binaryString = atob(r.data);
                const bytes = new Uint8Array(binaryString.length);
                for (let i = 0; i < binaryString.length; i++) {
                  bytes[i] = binaryString.charCodeAt(i);
                }
                const blob = new Blob([bytes], { type: 'audio/wav' });
                return URL.createObjectURL(blob);
              } catch (e) {
                console.error("Failed to decode audio data", e);
                return null;
              }
            }
            return null;
          })
          .filter(url => url !== null) || [];

        const details = result.executionDetails || [];

        if (details.length > 0) {
          addMessage('task-report', 'Task Execution Report', {
            report: {
              toolName: "Tool Executions",
              status: 'success',
              executions: details
            }
          });
        } else if (result.toolUsed) {
          addMessage('task-report', 'Task Execution Report', {
            report: {
              toolName: result.toolUsed,
              status: 'success',
              args: finalStatusArgs,
              amount: result.cost || finalStatusAmount,
              txHash: finalStatusTxHash,
              message: "Tool executed successfully."
            }
          });
        }

        const finalResponseText = result.finalResponse && result.finalResponse.trim() !== ''
          ? result.finalResponse
          : "task completed successfully (no text output)";

        addMessage('agent', finalResponseText, {
          agent: result.toolUsed ? `AI Agent (used ${result.toolUsed})` : 'AI Agent',
          agentIcon: 'AI',
          toolUsed: result.toolUsed,
          cost: result.cost,
          audioUrls: audioUrls
        });

        if (result.toolUsed && result.toolResponse) {
          addMessage('tool-response', 'Raw Tool Response', {
            toolName: result.toolUsed,
            response: result.toolResponse
          });
        }
      } else {
        addMessage('agent', `Error: ${result.finalResponse}`, {
          agent: 'AI Agent',
          agentIcon: 'AI'
        });
      }
    } catch (error) {
      console.error('Error processing message:', error);
      addMessage('system', `Error: ${error.message}`);

      if (processingStatus?.toolName) {
        addMessage('task-report', 'Task Failed', {
          report: {
            toolName: processingStatus.toolName,
            status: 'failed',
            args: processingStatus.args,
            amount: processingStatus.amount,
            message: error.message
          }
        });
      }

    } finally {
      setIsProcessing(false);
      setProcessingStatus(null);
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  return (
    <div className="agent-interface simplified" style={{ display: 'flex', flexDirection: 'row' }}>

      <ChatSidebar
        sessions={sessions}
        currentSessionId={currentSessionId}
        onSwitchSession={handleSwitchSession}
        onNewSession={createNewSession}
        onDeleteSession={deleteSession}
        isOpen={isSidebarOpen}
        footer={<><AgentWalletPanel /><WorkerPanel /></>}
      />

      <main className="chat-area-full" style={{ position: 'relative' }}>

        <button
          onClick={() => setIsSidebarOpen(!isSidebarOpen)}
          style={{
            position: 'absolute',
            top: '20px',
            left: '20px',
            zIndex: 10,
            background: 'var(--bg-tertiary)',
            border: '1px solid var(--glass-border)',
            padding: '8px',
            borderRadius: '50%',
            cursor: 'pointer',
            color: 'var(--text-secondary)'
          }}
        >
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2">
            {isSidebarOpen ? (
              <path d="M15 18l-6-6 6-6" />
            ) : (
              <path d="M9 18l6-6-6-6" />
            )}
          </svg>
        </button>

        <div className="messages-container">
          {messages.map((message) => (
            <div key={message.id} className={`message message-${message.type}`}>
              {message.type === 'user' && (
                <div className="message-content user-message">
                  <div className="message-text">{message.content}</div>
                  <div className="message-time">
                    {message.timestamp.toLocaleTimeString()}
                  </div>
                </div>
              )}

              {message.type === 'agent' && (
                <div className="message-content agent-message">
                  <div className="agent-avatar">{message.agentIcon}</div>
                  <div>
                    <div className="agent-label">
                      {message.agent}
                      {message.cost > 0 && ` • Cost: ${formatSFUEL(message.cost)}`}
                    </div>
                    <div className="message-text">
                      <MarkdownRenderer content={message.content} />
                    </div>
                    {message.audioUrls && message.audioUrls.length > 0 && (
                      <div className="audio-player-container">
                        <div className="audio-source-label">
                          {message.audioUrls.length > 1 ? 'Audio responses:' : 'Audio from tool response:'}
                        </div>
                        {message.audioUrls.map((url, index) => (
                          <div key={url} style={{ marginBottom: '8px' }}>
                            {message.audioUrls.length > 1 && <div style={{ fontSize: '0.8em', opacity: 0.7 }}>Clip {index + 1}</div>}
                            <audio
                              controls
                              className="audio-player"
                              src={url}
                            >
                              Your browser does not support the audio element.
                            </audio>
                          </div>
                        ))}
                      </div>
                    )}
                    {!message.audioUrls && message.audioUrl && (
                      <div className="audio-player-container">
                        <div className="audio-source-label">Audio from tool response:</div>
                        <audio
                          controls
                          className="audio-player"
                          key={message.audioUrl}
                          src={message.audioUrl}
                        >
                          Your browser does not support the audio element.
                        </audio>
                      </div>
                    )}
                    {audioBlobs[message.id] && (
                      <div className="audio-player-container">
                        <audio
                          controls
                          className="audio-player"
                          key={audioBlobs[message.id]}
                          src={audioBlobs[message.id]}
                        >
                          Your browser does not support the audio element.
                        </audio>
                      </div>
                    )}
                    <div className="message-actions">
                    </div>
                    <div className="message-time">
                      {message.timestamp.toLocaleTimeString()}
                    </div>
                  </div>
                </div>
              )}

              {message.type === 'system' && (
                <div className="message-content system-message">
                  <div className="message-text">{message.content}</div>
                </div>
              )}

              {message.type === 'tool-response' && (
                <div className="message-content tool-response-message">
                  <GlassCard className="tool-response-card">
                    <div
                      className="tool-response-header"
                      onClick={() => {
                        setExpandedResponses(prev => {
                          const newSet = new Set(prev);
                          if (newSet.has(message.id)) {
                            newSet.delete(message.id);
                          } else {
                            newSet.add(message.id);
                          }
                          return newSet;
                        });
                      }}
                      style={{ cursor: 'pointer' }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                        <div className="tool-response-icon">
                          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M4 7h16M4 12h16M4 17h16" />
                          </svg>
                        </div>
                        <div>
                          <h4>Raw Tool Response</h4>
                          <p className="tool-response-subtitle">
                            Tool: <strong>{message.toolName}</strong>
                          </p>
                        </div>
                      </div>
                      <div className={`dropdown-arrow ${expandedResponses.has(message.id) ? 'expanded' : ''}`}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                          <path d="M6 9l6 6 6-6" />
                        </svg>
                      </div>
                    </div>
                    {expandedResponses.has(message.id) && (
                      <div className="tool-response-content">
                        <pre className="json-response">
                          {JSON.stringify(message.response, null, 2)}
                        </pre>
                      </div>
                    )}
                  </GlassCard>
                </div>
              )}

              {message.type === 'task-report' && (
                <TaskReport report={message.report} />
              )}

            </div>
          ))}

          {processingStatus && (
            <ProcessingMessage status={processingStatus} />
          )}

          <div ref={messagesEndRef} />
        </div>

        <div className="input-area">
          <div className="input-container">
            <textarea
              ref={inputRef}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="Ask me anything... (e.g., 'What's the weather in San Francisco?')"
              rows="1"
              disabled={isProcessing || !isInitialized}
            />
            <div className="input-footer">
              <div className="estimated-cost">
                {isInitialized
                  ? `${availableTools.length} tools available • AI will select the best one`
                  : 'Initializing...'}
              </div>
              <Button
                variant="primary"
                onClick={handleSendMessage}
                disabled={!inputValue.trim() || isProcessing || !isInitialized}
              >
                {isProcessing ? 'Processing...' : 'Send'}
              </Button>
            </div>
          </div>
        </div>
      </main >

      <div className="notification-container" style={{
        position: 'fixed',
        bottom: '20px',
        right: '20px',
        display: 'flex',
        flexDirection: 'column',
        gap: '10px',
        zIndex: 9999,
        pointerEvents: 'none'
      }}>
        {notifications.map(notif => (
          <div key={notif.id} style={{ pointerEvents: 'auto' }}>
            <Notification
              message={notif.message}
              subtext={notif.subtext}
              type={notif.type}
              duration={0}
              onClose={() => removeNotification(notif.id)}
              args={notif.args}
            />
          </div>
        ))
        }
      </div >
    </div >
  );
};

export default AgentInterface;
