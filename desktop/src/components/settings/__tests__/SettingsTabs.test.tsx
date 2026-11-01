/**
 * The settings tabs after the merge (design 2026-11-01-settings-account-merge §3, §6).
 * Questions: are Hive Accounts, Core Engine and Capabilities gone and the two renamed;
 * do the old deep links land where their content went?
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import SettingsTabs from '../SettingsTabs';

vi.mock('../GeneralTab', () => ({ default: () => <div data-testid="tab-general" /> }));
vi.mock('../AwsAccountTab', () => ({ default: () => <div data-testid="tab-aws-account" /> }));
vi.mock('../AIModelsTab', () => ({ default: () => <div data-testid="tab-ai-models" /> }));
vi.mock('../ChannelsTab', () => ({ default: () => <div data-testid="tab-channels" /> }));
vi.mock('../BackupTab', () => ({ default: () => <div data-testid="tab-backup" /> }));
vi.mock('../SystemTab', () => ({ default: () => <div data-testid="tab-system" /> }));
vi.mock('../AboutTab', () => ({ default: () => <div data-testid="tab-about" /> }));

describe('SettingsTabs', () => {
  it('has the merged tab list', () => {
    render(<SettingsTabs />);
    for (const label of ['General', 'Swarm AWS Account', 'LLM Models', 'Channels', 'Backup', 'System', 'About']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    for (const gone of ['Hive Accounts', 'Core Engine', 'Capabilities', 'SwarmAI Account', 'AI & Models']) {
      expect(screen.queryByText(gone)).toBeNull();
    }
  });

  it.each([['hive', 'aws-account'], ['engine', 'general'], ['capabilities', 'general'], ['ai-models', 'ai-models'], ['nope', 'general']])(
    'deep link %s opens %s', async (initial, shown) => {
      render(<SettingsTabs initialTab={initial} />);
      expect(await screen.findByTestId(`tab-${shown}`)).toBeInTheDocument();
    });
});
