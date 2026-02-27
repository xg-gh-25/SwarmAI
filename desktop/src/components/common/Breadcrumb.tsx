import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

interface BreadcrumbProps {
  currentPage: string;
}

export default function Breadcrumb({ currentPage }: BreadcrumbProps) {
  const { t } = useTranslation();

  return (
    <nav className="flex items-center gap-2 mb-4 text-sm">
      <Link
        to="/dashboard"
        className="flex items-center gap-1 text-[var(--color-text-muted)] hover:text-primary transition-colors"
      >
        <span className="material-symbols-outlined text-lg">arrow_back</span>
        <span>{t('nav.swarmcore')}</span>
      </Link>
      <span className="text-[var(--color-text-muted)]">/</span>
      <span className="text-[var(--color-text)]">{currentPage}</span>
    </nav>
  );
}
