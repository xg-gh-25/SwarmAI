/**
 * GeneralTab — the repository workspace (repos_dir): where `bind --url` clones a Brain's
 * code repository (fixing-spec 2026-11-02-new-brain-setup G1). The field shows the
 * backend's value and saves the trimmed one through PUT /settings.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import GeneralTab from '../GeneralTab';

const mockGet = vi.fn();
const mockUpdate = vi.fn();
vi.mock('../../../services/settings', () => ({
  settingsService: {
    getAPIConfiguration: (...a: unknown[]) => mockGet(...a),
    updateAPIConfiguration: (...a: unknown[]) => mockUpdate(...a),
  },
}));
vi.mock('../../../contexts/ThemeContext', () => ({
  useTheme: () => ({ theme: 'dark', setTheme: vi.fn(), accentColor: 'blue', setAccentColor: vi.fn() }),
  ACCENT_PRESETS: [],
}));

describe('GeneralTab repository workspace', () => {
  beforeEach(() => {
    mockGet.mockReset();
    mockUpdate.mockReset();
  });

  it('shows the configured folder and saves the edited one', async () => {
    mockGet.mockResolvedValue({ reposDir: '~/SwarmAI-Repos' });
    mockUpdate.mockResolvedValue({ reposDir: '~/Desktop/SwarmAI-Workspace' });
    render(<GeneralTab />);
    const input = await screen.findByLabelText('Repository workspace');
    await waitFor(() => expect((input as HTMLInputElement).value).toBe('~/SwarmAI-Repos'));
    fireEvent.change(input, { target: { value: '  ~/Desktop/SwarmAI-Workspace ' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith({ reposDir: '~/Desktop/SwarmAI-Workspace' }));
    expect(await screen.findByText('Saved')).toBeTruthy();
  });

  it('an empty field resets to the default the backend returns, and a refusal says so', async () => {
    mockGet.mockResolvedValue({ reposDir: '~/elsewhere' });
    mockUpdate.mockResolvedValueOnce({ reposDir: '~/SwarmAI-Repos' }).mockRejectedValueOnce(new Error('422'));
    render(<GeneralTab />);
    const input = await screen.findByLabelText('Repository workspace');
    await waitFor(() => expect((input as HTMLInputElement).value).toBe('~/elsewhere'));
    fireEvent.change(input, { target: { value: '' } });
    fireEvent.click(screen.getByText('Save'));
    await waitFor(() => expect(mockUpdate).toHaveBeenCalledWith({ reposDir: '' }));
    await waitFor(() => expect((input as HTMLInputElement).value).toBe('~/SwarmAI-Repos'));
    fireEvent.change(input, { target: { value: '~/.swarm-ai/SwarmWS' } });
    fireEvent.click(screen.getByText('Save'));
    expect(await screen.findByText('Could not save the folder')).toBeTruthy();
  });
});
