/**
 * Settings → LLM Models, where the model is called from (design 2026-11-01-settings-account-merge §3).
 * Questions: are there exactly two choices (account, Bedrock key), with the old Anthropic
 * key shown only to someone still on it; does the account choice go through the shared
 * useForChat and refuse without an account; does the key choice open the verify-then-store
 * form; and is there no AWS sign-in control here?
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import ModelProvider from '../ModelProvider';
import type { AwsAccountView } from '../../../services/awsAccount';

const mockGet = vi.fn();
const mockUseForChat = vi.fn();
vi.mock('../../../services/awsAccount', () => ({
  awsAccountService: { get: (...a: unknown[]) => mockGet(...a), useForChat: (...a: unknown[]) => mockUseForChat(...a) },
}));
vi.mock('../../awsAccount/ConnectAccount', () => ({
  BedrockKeyForm: (p: { region: string; context: string }) => <div data-testid="key-form" data-region={p.region} data-context={p.context} />,
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));

const account = { source: 'ada', account_id: '533267412361', region: 'us-east-1', context: 'internal' } as AwsAccountView['account'];
const view = (over: Partial<AwsAccountView> = {}) => ({ state: 'connected', account, chat_via: 'aws', region: 'eu-west-1', ...over }) as AwsAccountView;

beforeEach(() => { mockGet.mockReset(); mockUseForChat.mockReset(); });

describe('ModelProvider', () => {
  it('offers two choices, the account one selected, and no sign-in controls', async () => {
    mockGet.mockResolvedValue(view());
    render(<ModelProvider />);
    const aws = await screen.findByTestId('model-provider-aws');
    expect(aws).toHaveAttribute('aria-pressed', 'true');
    expect(aws).toHaveTextContent('533267412361 · eu-west-1');
    expect(screen.getByTestId('model-provider-key')).toHaveAttribute('aria-pressed', 'false');
    expect(screen.queryByTestId('model-provider-anthropic')).toBeNull();
    expect(screen.queryByText(/ada|mwinit|Verify Connection/i)).toBeNull();
  });

  it('moving chat back to the account goes through useForChat', async () => {
    mockGet.mockResolvedValue(view({ chat_via: 'bedrock_key' }));
    mockUseForChat.mockResolvedValue(view());
    render(<ModelProvider />);
    fireEvent.click(await screen.findByTestId('model-provider-aws'));
    await waitFor(() => expect(mockUseForChat).toHaveBeenCalledWith(account));
    await waitFor(() => expect(screen.getByTestId('model-provider-aws')).toHaveAttribute('aria-pressed', 'true'));
  });

  it('without an account the account choice cannot be picked and says where to connect', async () => {
    mockGet.mockResolvedValue(view({ account: null, state: 'unconnected', chat_via: 'bedrock_key' }));
    render(<ModelProvider />);
    const aws = await screen.findByTestId('model-provider-aws');
    expect(aws).toBeDisabled();
    expect(aws).toHaveTextContent('settings.modelProvider.awsNone');
  });

  it('the key choice opens the shared form in the main region', async () => {
    mockGet.mockResolvedValue(view());
    render(<ModelProvider />);
    fireEvent.click(await screen.findByTestId('model-provider-key'));
    const form = screen.getByTestId('key-form');
    expect(form).toHaveAttribute('data-region', 'eu-west-1');
    expect(form).toHaveAttribute('data-context', 'internal');
  });

  it('someone still on an Anthropic key sees it as the current, old choice', async () => {
    mockGet.mockResolvedValue(view({ chat_via: 'anthropic' }));
    render(<ModelProvider />);
    expect(await screen.findByTestId('model-provider-anthropic')).toHaveTextContent('settings.modelProvider.anthropic');
    expect(screen.getByTestId('model-provider-aws')).toHaveAttribute('aria-pressed', 'false');
  });

  it('the key form can be closed again, by cancel or by the account choice', async () => {
    mockGet.mockResolvedValue(view());
    render(<ModelProvider />);
    fireEvent.click(await screen.findByTestId('model-provider-key'));
    fireEvent.click(screen.getByTestId('model-provider-key-cancel'));
    expect(screen.queryByTestId('key-form')).toBeNull();
    fireEvent.click(screen.getByTestId('model-provider-key'));
    fireEvent.click(screen.getByTestId('model-provider-aws'));
    expect(screen.queryByTestId('key-form')).toBeNull();
  });
});
