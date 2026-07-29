import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Navigation from './components/Navigation';
import Landing from './pages/Landing';
import AgentInterface from './pages/AgentInterface';
import Marketplace from './pages/Marketplace';
import AddTool from './pages/AddTool';
import './index.css';

function App() {
  return (
    <Router>
      <div className="app">
        <Navigation />
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/agent" element={<AgentInterface />} />
          <Route path="/marketplace" element={<Marketplace />} />
          <Route path="/add-tool" element={<AddTool />} />
        </Routes>
      </div>
    </Router>
  );
}

export default App;
