import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import Lyrbot from './components/Lyrbot';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
    <Lyrbot />
  </React.StrictMode>,
);
