/**
 * AIModelsTab — Thinking controls follow what the DEFAULT model accepts.
 *
 * The backend (model_registry via GET/PUT /api/settings) is the one authority:
 * `supportedThinkingModes` + `effectiveThinkingMode`. The tab must render only
 * supported modes, select the effective one, say when the stored mode is not in
 * effect, refresh after ANY settings PUT (e.g. a default-model change), and fall
 * back to all three modes when talking to an older backend without the fields.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import AIModelsTab from '../AIModelsTab';

const mockGet = vi.fn();
const mockUpdate = vi.fn();
const mockGetBedrockModels = vi.fn();

vi.mock('../../../services/settings', () => ({
  settingsService: {
    getAPIConfiguration: (...a: unknown[]) => mockGet(...a),
    updateAPIConfiguration: (...a: unknown[]) => mockUpdate(...a),
    getBedrockModels: (...a: unknown[]) => mockGetBedrockModels(...a),
  },
}));
vi.mock('../AuthConfigPanel', () => ({ default: () => <div /> }));
vi.mock('@tanstack/react-query', () => ({ useQueryClient: () => ({ invalidateQueries: vi.fn() }) }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
vi.mock('../../common', () => ({
  Dropdown: ({ label, options, selectedId, onChange, disabled }: {
    label: string; options: { id: string }[]; selectedId: string | null;
    onChange: (id: string) => void; disabled?: boolean;
  }) => (
    <div data-testid={`dd-${label}`} data-selected={selectedId ?? ''} data-disabled={disabled ? 'true' : 'false'}
      data-options={options.map((o) => o.id).join(',')}>
      {options.map((o) => (
        <button key={o.id} onClick={() => onChange(o.id)}>{`${label}:${o.id}`}</button>
      ))}
    </div>
  ),
}));

const base = {
  availableModels: ['claude-opus-5-5', 'claude-opus-4-8'],
  defaultModel: 'claude-opus-5-5',
  thinkingMode: 'disabled',
  thinkingEffort: 'high',
  bedrockModelMap: {},
};

beforeEach(() => {
  vi.clearAllMocks();
});

const modeDropdown = () => screen.getByTestId('dd-Thinking Mode');

describe('AIModelsTab thinking controls', () => {
  it('offers only supported modes and explains a stored mode that is not in effect', async () => {
    mockGet.mockResolvedValue({ ...base, supportedThinkingModes: ['adaptive'], effectiveThinkingMode: 'adaptive' });
    render(<AIModelsTab />);
    await waitFor(() => expect(modeDropdown().dataset.options).toBe('adaptive'));
    expect(modeDropdown().dataset.selected).toBe('adaptive');
    expect(screen.getByTestId('thinking-mode-not-in-effect')).toHaveTextContent('Disabled');
  });

  it('falls back to all three modes when the backend omits the fields', async () => {
    mockGet.mockResolvedValue({ ...base, thinkingMode: 'enabled' });
    render(<AIModelsTab />);
    await waitFor(() => expect(modeDropdown().dataset.selected).toBe('enabled'));
    expect(modeDropdown().dataset.options).toBe('adaptive,enabled,disabled');
    expect(screen.queryByTestId('thinking-mode-not-in-effect')).toBeNull();
  });

  it('refreshes the thinking options after the default model changes', async () => {
    mockGet.mockResolvedValue({ ...base, supportedThinkingModes: ['adaptive'], effectiveThinkingMode: 'adaptive' });
    mockUpdate.mockResolvedValue({
      ...base, defaultModel: 'claude-opus-4-8',
      supportedThinkingModes: ['adaptive', 'enabled', 'disabled'], effectiveThinkingMode: 'disabled',
    });
    render(<AIModelsTab />);
    await waitFor(() => expect(modeDropdown().dataset.options).toBe('adaptive'));
    fireEvent.click(screen.getByText('settings.modelConfig.defaultModel:claude-opus-4-8'));
    await waitFor(() => expect(modeDropdown().dataset.options).toBe('adaptive,enabled,disabled'));
    expect(modeDropdown().dataset.selected).toBe('disabled');
    expect(screen.queryByTestId('thinking-mode-not-in-effect')).toBeNull();
  });

  it('picking the only supported mode saves it and clears the note', async () => {
    mockGet.mockResolvedValue({ ...base, supportedThinkingModes: ['adaptive'], effectiveThinkingMode: 'adaptive' });
    mockUpdate.mockResolvedValue({
      ...base, thinkingMode: 'adaptive', supportedThinkingModes: ['adaptive'], effectiveThinkingMode: 'adaptive',
    });
    render(<AIModelsTab />);
    await waitFor(() => screen.getByTestId('thinking-mode-not-in-effect'));
    fireEvent.click(screen.getByText('Thinking Mode:adaptive'));
    await waitFor(() => expect(screen.queryByTestId('thinking-mode-not-in-effect')).toBeNull());
    expect(mockUpdate).toHaveBeenCalledWith({ thinking_mode: 'adaptive' });
  });

  it('refreshes thinking state after an effort PUT', async () => {
    mockGet.mockResolvedValue({ ...base, defaultModel: 'claude-opus-4-8', thinkingMode: 'adaptive' });
    mockUpdate.mockResolvedValue({
      ...base, thinkingEffort: 'max', supportedThinkingModes: ['adaptive'], effectiveThinkingMode: 'adaptive',
    });
    render(<AIModelsTab />);
    await waitFor(() => expect(modeDropdown().dataset.options).toBe('adaptive,enabled,disabled'));
    fireEvent.click(screen.getByText('Thinking Effort:max'));
    await waitFor(() => expect(modeDropdown().dataset.options).toBe('adaptive'));
  });

  it('refreshes thinking state after adding a discovered model', async () => {
    mockGet.mockResolvedValue({ ...base, defaultModel: 'claude-opus-4-8', thinkingMode: 'adaptive' });
    mockGetBedrockModels.mockResolvedValue({
      available: true, error: null,
      models: [{ shortName: 'claude-sonnet-5', bedrockId: 'us.anthropic.claude-sonnet-5', isNew: true }],
    });
    mockUpdate.mockResolvedValue({ ...base, supportedThinkingModes: ['adaptive'], effectiveThinkingMode: 'adaptive' });
    render(<AIModelsTab />);
    await waitFor(() => expect(modeDropdown().dataset.options).toBe('adaptive,enabled,disabled'));
    fireEvent.click(screen.getByText('Refresh from Bedrock'));
    await waitFor(() => screen.getByText('claude-sonnet-5'));
    const addButtons = screen.getAllByText('settings.modelConfig.addModel');
    fireEvent.click(addButtons[addButtons.length - 1]);
    await waitFor(() => expect(modeDropdown().dataset.options).toBe('adaptive'));
  });

  it('disables the effort picker when thinking is effectively off', async () => {
    mockGet.mockResolvedValue({
      ...base, defaultModel: 'claude-opus-4-8',
      supportedThinkingModes: ['adaptive', 'enabled', 'disabled'], effectiveThinkingMode: 'disabled',
    });
    render(<AIModelsTab />);
    await waitFor(() => expect(screen.getByTestId('dd-Thinking Effort').dataset.disabled).toBe('true'));
  });

  it('never shows the note for an unknown stored mode', async () => {
    mockGet.mockResolvedValue({
      ...base, thinkingMode: 'bogus', supportedThinkingModes: ['adaptive'], effectiveThinkingMode: 'adaptive',
    });
    render(<AIModelsTab />);
    await waitFor(() => expect(modeDropdown().dataset.selected).toBe('adaptive'));
    expect(screen.queryByTestId('thinking-mode-not-in-effect')).toBeNull();
  });
});
