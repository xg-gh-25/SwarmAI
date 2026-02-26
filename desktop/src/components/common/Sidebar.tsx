import { NavLink, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useRunningTaskCount } from '../../hooks/useRunningTaskCount';

interface NavItem {
  path: string;
  labelKey: string;
  icon: string;
}

const GITHUB_URL = 'https://github.com/xiehust/owork.git';

// GitHub SVG icon component
const GitHubIcon = ({ className }: { className?: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="currentColor"
    xmlns="http://www.w3.org/2000/svg"
  >
    <path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23A11.509 11.509 0 0112 5.803c1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z"/>
  </svg>
);

interface SidebarProps {
  collapsed?: boolean;
  onClose?: () => void;
  isOverlay?: boolean;
}

export default function Sidebar({ collapsed, onClose, isOverlay }: SidebarProps) {
  const { t } = useTranslation();
  const location = useLocation();

  const { count: runningTaskCount } = useRunningTaskCount();

  const navItems: NavItem[] = [
    { path: '/chat', labelKey: 'nav.chat', icon: 'chat' },
    { path: '/tasks', labelKey: 'nav.tasks', icon: 'task_alt' },
    { path: '/agents', labelKey: 'nav.agents', icon: 'smart_toy' },
    { path: '/skills', labelKey: 'nav.skills', icon: 'construction' },
    { path: '/plugins', labelKey: 'nav.plugins', icon: 'extension' },
    { path: '/channels', labelKey: 'nav.channels', icon: 'hub' },
    { path: '/mcp', labelKey: 'nav.mcp', icon: 'dns' },
  ];

  const bottomNavItems: NavItem[] = [
    { path: '/settings', labelKey: 'nav.settings', icon: 'settings' },
  ];

  const isActive = (path: string) => {
    if (path === '/') {
      return location.pathname === '/';
    }
    return location.pathname.startsWith(path);
  };

  const handleNavClick = () => {
    if (isOverlay && onClose) {
      onClose();
    }
  };

  // Icon-only collapsed mode
  if (collapsed) {
    return (
      <aside className="w-16 bg-[var(--color-bg)] border-r border-[var(--color-border)] flex flex-col flex-shrink-0">
        {/* Dashboard - Top Icon */}
        <div className="h-16 flex items-center justify-center border-b border-[var(--color-border)]">
          <NavLink
            to="/"
            title={t('nav.dashboard')}
            className={clsx(
              'w-10 h-10 rounded-xl flex items-center justify-center transition-colors',
              isActive('/')
                ? 'bg-primary text-white'
                : 'bg-[var(--color-hover)] text-[var(--color-text-muted)] hover:bg-primary/20 hover:text-primary'
            )}
          >
            <span className="material-symbols-outlined text-xl">dashboard</span>
          </NavLink>
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-4 px-2 space-y-1">
          {navItems.map((item) => (
            <div key={item.path} className="relative">
              <NavLink
                to={item.path}
                title={t(item.labelKey)}
                className={clsx(
                  'flex items-center justify-center w-12 h-12 rounded-xl transition-colors',
                  isActive(item.path)
                    ? 'bg-primary/20 text-primary'
                    : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
                )}
              >
                <span className="material-symbols-outlined text-2xl">{item.icon}</span>
              </NavLink>
              {item.path === '/tasks' && runningTaskCount > 0 && (
                <span className="absolute -top-1 -right-1 w-5 h-5 bg-primary text-white text-xs font-bold rounded-full flex items-center justify-center">
                  {runningTaskCount > 9 ? '9+' : runningTaskCount}
                </span>
              )}
            </div>
          ))}
        </nav>

        {/* Bottom navigation */}
        <div className="py-4 px-2 border-t border-[var(--color-border)] space-y-1">
          {bottomNavItems.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              title={t(item.labelKey)}
              className={clsx(
                'flex items-center justify-center w-12 h-12 rounded-xl transition-colors',
                isActive(item.path)
                  ? 'bg-primary/20 text-primary'
                  : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
              )}
            >
              <span className="material-symbols-outlined text-2xl">{item.icon}</span>
            </NavLink>
          ))}

          {/* GitHub Link */}
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            title="GitHub"
            className="flex items-center justify-center w-12 h-12 rounded-xl transition-colors text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]"
          >
            <GitHubIcon className="w-6 h-6" />
          </a>

          {/* User Avatar */}
          <div className="flex items-center justify-center pt-2">
            <div className="w-10 h-10 rounded-full bg-gradient-to-br from-blue-400 to-purple-500 flex items-center justify-center ring-2 ring-dark-border">
              <span className="material-symbols-outlined text-white text-lg">person</span>
            </div>
          </div>
        </div>
      </aside>
    );
  }

  // Full expanded mode (overlay)
  return (
    <aside
      className={clsx(
        'w-64 bg-[var(--color-bg)] border-r border-[var(--color-border)] flex flex-col',
        isOverlay && 'fixed left-0 top-0 h-full z-50 animate-slide-in-left shadow-2xl'
      )}
    >
      {/* Header with Dashboard */}
      <div className="h-16 flex items-center px-4 border-b border-[var(--color-border)]">
        <NavLink
          to="/"
          onClick={handleNavClick}
          className="flex items-center gap-3 flex-1"
        >
          <div className={clsx(
            'w-10 h-10 rounded-xl flex items-center justify-center transition-colors',
            isActive('/') ? 'bg-primary text-white' : 'bg-[var(--color-hover)] text-[var(--color-text-muted)]'
          )}>
            <span className="material-symbols-outlined">dashboard</span>
          </div>
          <div>
            <h1 className="font-semibold text-[var(--color-text)]">Agent Platform</h1>
            <p className="text-xs text-[var(--color-text-muted)]">{t('nav.dashboard')}</p>
          </div>
        </NavLink>
        {isOverlay && onClose && (
          <button
            onClick={onClose}
            className="p-2 rounded-lg text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)] transition-colors"
            aria-label={t('common.button.close')}
          >
            <span className="material-symbols-outlined">close</span>
          </button>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-4 px-3 space-y-1">
        {navItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            onClick={handleNavClick}
            className={clsx(
              'flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors',
              isActive(item.path)
                ? 'bg-primary text-white'
                : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
            )}
          >
            <span className="material-symbols-outlined text-xl">{item.icon}</span>
            <span className="text-sm font-medium flex-1">{t(item.labelKey)}</span>
            {item.path === '/tasks' && runningTaskCount > 0 && (
              <span className={clsx(
                'w-5 h-5 text-xs font-bold rounded-full flex items-center justify-center',
                isActive(item.path)
                  ? 'bg-white/20 text-white'
                  : 'bg-primary/20 text-primary'
              )}>
                {runningTaskCount > 9 ? '9+' : runningTaskCount}
              </span>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Bottom navigation */}
      <div className="py-4 px-3 border-t border-[var(--color-border)] space-y-1">
        {bottomNavItems.map((item) => (
          <NavLink
            key={item.path}
            to={item.path}
            onClick={handleNavClick}
            className={clsx(
              'flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors',
              isActive(item.path)
                ? 'bg-primary text-white'
                : 'text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]'
            )}
          >
            <span className="material-symbols-outlined text-xl">{item.icon}</span>
            <span className="text-sm font-medium">{t(item.labelKey)}</span>
          </NavLink>
        ))}

        {/* GitHub Link */}
        <a
          href={GITHUB_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors text-[var(--color-text-muted)] hover:bg-[var(--color-hover)] hover:text-[var(--color-text)]"
        >
          <GitHubIcon className="w-5 h-5" />
          <span className="text-sm font-medium">GitHub</span>
        </a>

        {/* User Profile */}
        <div className="flex items-center gap-3 px-3 py-2.5 mt-2">
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-400 to-purple-500 flex items-center justify-center">
            <span className="material-symbols-outlined text-white text-sm">person</span>
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-[var(--color-text)] truncate">{t('common.label.user')}</p>
            <p className="text-xs text-[var(--color-text-muted)] truncate">{t('nav.settings')}</p>
          </div>
        </div>
      </div>
    </aside>
  );
}
