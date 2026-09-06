import { createRoot } from 'react-dom/client';
import { LocaleProvider } from './i18n';
import App from './App';
import './styles.css';

createRoot(document.getElementById('root')!).render(<LocaleProvider><App /></LocaleProvider>);
