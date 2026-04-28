/**
 * Skills management page for the SwarmAI desktop app.
 *
 * Displays the three-tier filesystem-based skill list with source tier badges,
 * supports rescan, AI-powered skill generation, and delete for user skills.
 *
 * Key exports:
 * - ``SkillsPage``          — Main page component
 * - ``GenerateSkillForm``   — AI skill generation chat form
 */
import { useState, useEffect, useRef, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { SearchBar, Button, Modal, SkeletonTable, ResizableTable, ResizableTableCell, ConfirmDialog, AskUserQuestion, Dropdown, MarkdownRenderer, Breadcrumb } from '../components/common';
import type { Skill, StreamEvent, ContentBlock, AskUserQuestion as AskUserQuestionType } from '../types';
import { skillsService } from '../services/skills';
import { chatService } from '../services/chat';
import { settingsService } from '../services/settings';
import { Spinner } from '../components/common';

// Helper to convert model ID to dropdown option
const modelIdToOption = (id: string) => ({
  id,
  name: id
    .split(/[-.]/)
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' '),
  description: id,
});

// Signature skills — SwarmAI's two flagship pipelines.
// Shown first with a ⭐ badge + tagline. Key = folder name, value = tagline.
const FEATURED_SKILLS = new Map<string, string>([
  ['s_autonomous-pipeline', 'One sentence in → PR-ready code out. The AIDLC delivery engine.'],
  ['s_pollinate',           'Your message, their attention, the right format. Media value delivery.'],
]);

// Table column configuration - will be translated via hook
const getSkillColumns = (t: (key: string) => string) => [
  { key: 'name', header: t('skills.table.name'), initialWidth: 200, minWidth: 120 },
  { key: 'description', header: t('skills.table.description'), initialWidth: 280, minWidth: 150 },
  { key: 'source', header: t('skills.table.source'), initialWidth: 180, minWidth: 120 },
  { key: 'version', header: t('skills.table.version'), initialWidth: 120, minWidth: 80 },
  { key: 'actions', header: t('skills.table.actions'), initialWidth: 120, minWidth: 100, align: 'right' as const },
];

// Get source display for a skill based on filesystem tier
function getSourceDisplay(skill: Skill): { label: string; icon: string; color: string } {
  switch (skill.sourceTier) {
    case 'built-in':
      return {
        label: 'Built-in',
        icon: 'verified',
        color: 'text-cyan-400',
      };
    case 'plugin':
      return {
        label: 'Plugin',
        icon: 'extension',
        color: 'text-purple-400',
      };
    case 'user':
    default:
      return {
        label: 'User',
        icon: 'person',
        color: 'text-[var(--color-text-muted)]',
      };
  }
}

export default function SkillsPage() {
  const { t } = useTranslation();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [isGenerateModalOpen, setIsGenerateModalOpen] = useState(false);
  const [isInitialLoading, setIsInitialLoading] = useState(true);
  const [isRescanning, setIsRescanning] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Skill | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  // Get translated columns
  const SKILL_COLUMNS = getSkillColumns(t);

  // Fetch skills on mount
  useEffect(() => {
    const fetchSkills = async () => {
      try {
        const data = await skillsService.list();
        setSkills(data);
      } catch (error) {
        console.error('Failed to fetch skills:', error);
      } finally {
        setIsInitialLoading(false);
      }
    };
    fetchSkills();
  }, []);

  const filteredSkills = useMemo(() => {
    const filtered = skills.filter((skill) =>
      skill.name.toLowerCase().includes(searchQuery.toLowerCase())
    );
    // Featured skills float to top, then alphabetical within each group
    return filtered.sort((a, b) => {
      const aFeatured = FEATURED_SKILLS.has(a.folderName);
      const bFeatured = FEATURED_SKILLS.has(b.folderName);
      if (aFeatured !== bFeatured) return aFeatured ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
  }, [skills, searchQuery]);

  const handleDeleteClick = (skill: Skill) => {
    setDeleteTarget(skill);
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    setIsDeleting(true);
    try {
      await skillsService.delete(deleteTarget.folderName);
      setSkills((prev) => prev.filter((skill) => skill.folderName !== deleteTarget.folderName));
      setDeleteTarget(null);
    } catch (error) {
      console.error('Failed to delete skill:', error);
    } finally {
      setIsDeleting(false);
    }
  };

  const handleGenerate = async (skill: Skill) => {
    // Skill is already created and saved by the GenerateSkillForm component
    // Check if skill already exists (by folderName) and update, otherwise add
    setSkills((prev) => {
      const existingIndex = prev.findIndex((s) => s.folderName === skill.folderName);
      if (existingIndex >= 0) {
        const updated = [...prev];
        updated[existingIndex] = skill;
        return updated;
      }
      return [...prev, skill];
    });
    setIsGenerateModalOpen(false);
  };

  const handleRescan = async () => {
    setIsRescanning(true);
    try {
      const data = await skillsService.rescan();
      setSkills(data);
    } catch (error) {
      console.error('Failed to rescan skills:', error);
    } finally {
      setIsRescanning(false);
    }
  };

  return (
    <div className="p-8">
      <Breadcrumb currentPage={t('skills.title')} />

      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-[var(--color-text)]">{t('skills.title')}</h1>
      </div>

      {/* Toolbar */}
      <div className="flex items-center justify-between mb-6">
        <SearchBar
          value={searchQuery}
          onChange={setSearchQuery}
          placeholder={t('skills.searchPlaceholder')}
          className="w-96"
        />

        <div className="flex gap-3">
          <Button
            variant="secondary"
            icon="refresh"
            onClick={handleRescan}
            isLoading={isRescanning}
            disabled={isRescanning}
          >
            {isRescanning ? t('common.status.loading') : t('skills.rescan')}
          </Button>
          <Button icon="auto_awesome" onClick={() => setIsGenerateModalOpen(true)}>
            {t('skills.createSkill')}
          </Button>
        </div>
      </div>

      {/* Skills Table */}
      <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-xl overflow-hidden">
        {isInitialLoading ? (
          <SkeletonTable rows={5} columns={5} />
        ) : (
          <ResizableTable columns={SKILL_COLUMNS}>
            {filteredSkills.map((skill) => (
              <tr
                key={skill.folderName}
                className="border-b border-[var(--color-border)] hover:bg-[var(--color-hover)] transition-colors"
              >
                <ResizableTableCell>
                  <span className="text-[var(--color-text)] font-medium">
                    {FEATURED_SKILLS.has(skill.folderName) && (
                      <span className="text-amber-400 mr-1.5" title="Signature skill">⭐</span>
                    )}
                    {skill.name}
                  </span>
                </ResizableTableCell>
                <ResizableTableCell>
                  {FEATURED_SKILLS.has(skill.folderName) ? (
                    <div className="flex flex-col gap-0.5">
                      <span className="text-amber-300/90 text-xs font-medium italic">
                        {FEATURED_SKILLS.get(skill.folderName)}
                      </span>
                      <span className="text-[var(--color-text-muted)] text-[11px] leading-tight">
                        {skill.description}
                      </span>
                    </div>
                  ) : (
                    <span className="text-[var(--color-text-muted)]" title={skill.description}>
                      {skill.description}
                    </span>
                  )}
                </ResizableTableCell>
                <ResizableTableCell>
                  {(() => {
                    const source = getSourceDisplay(skill);
                    return (
                      <div className="flex items-center gap-1.5">
                        <span className={`material-symbols-outlined text-sm ${source.color}`}>
                          {source.icon}
                        </span>
                        <span className={`text-sm ${source.color}`}>{source.label}</span>
                      </div>
                    );
                  })()}
                </ResizableTableCell>
                <ResizableTableCell>
                  <span className="px-2 py-0.5 bg-primary/20 text-primary text-xs font-medium rounded">
                    v{skill.version || '1.0.0'}
                  </span>
                </ResizableTableCell>
                <ResizableTableCell align="right">
                  <div className="flex items-center justify-end gap-1">
                    {/* Only show delete for user skills (not readOnly) */}
                    {!skill.readOnly && (
                      <button
                        onClick={() => handleDeleteClick(skill)}
                        className="p-1.5 rounded-lg text-[var(--color-text-muted)] hover:text-status-error hover:bg-status-error/10 transition-colors"
                        title={t('skills.deleteSkill')}
                      >
                        <span className="material-symbols-outlined text-lg">delete</span>
                      </button>
                    )}
                    {/* For read-only skills, show tier info */}
                    {skill.readOnly && (
                      <span className="text-xs text-[var(--color-text-muted)]">
                        {skill.sourceTier === 'built-in' ? t('skills.source.builtIn') : t('skills.source.plugin')}
                      </span>
                    )}
                  </div>
                </ResizableTableCell>
              </tr>
            ))}

            {filteredSkills.length === 0 && (
              <tr>
                <td colSpan={5} className="px-6 py-12 text-center">
                  <span className="material-symbols-outlined text-4xl text-[var(--color-text-muted)] mb-2">
                    construction
                  </span>
                  <p className="text-[var(--color-text-muted)]">{t('skills.noSkills')}</p>
                </td>
              </tr>
            )}
          </ResizableTable>
        )}
      </div>

      {/* Generate Modal */}
      <Modal
        isOpen={isGenerateModalOpen}
        onClose={() => setIsGenerateModalOpen(false)}
        title={t('skills.create.title')}
        size="3xl"
      >
        <GenerateSkillForm
          onClose={() => setIsGenerateModalOpen(false)}
          onGenerate={handleGenerate}
        />
      </Modal>

      {/* Delete Confirmation Dialog */}
      <ConfirmDialog
        isOpen={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirm}
        title={t('skills.deleteSkill')}
        message={
          <>
            {t('skills.deleteConfirm', { name: '' })}<strong className="text-[var(--color-text)]">{deleteTarget?.name}</strong>?
            <br />
            <span className="text-sm text-status-error font-medium">
              {t('common.message.cannotUndo')}
            </span>
          </>
        }
        confirmText={t('common.button.delete')}
        cancelText={t('common.button.cancel')}
        isLoading={isDeleting}
      />

    </div>
  );
}

// Generate Skill Form Component - Now with Agent Chat
function GenerateSkillForm({
  onClose,
  onGenerate,
}: {
  onClose: () => void;
  onGenerate: (skill: Skill) => void;
}) {
  const { t } = useTranslation();

  // Fetch API config to get model list
  const { data: apiConfig } = useQuery({
    queryKey: ['apiConfig'],
    queryFn: settingsService.getAPIConfiguration,
  });
  const availableModels = useMemo(() => apiConfig?.availableModels ?? [], [apiConfig?.availableModels]);
  const defaultModelFromSettings = apiConfig?.defaultModel ?? '';
  const modelOptions = useMemo(() => availableModels.map(modelIdToOption), [availableModels]);

  // Phase 1: Input form
  const [name, setName] = useState('');
  const [nameError, setNameError] = useState<string | null>(null);
  const [description, setDescription] = useState('');
  const [selectedModel, setSelectedModel] = useState('');

  // Set default model from settings when loaded
  useEffect(() => {
    if (!selectedModel && defaultModelFromSettings) {
      setSelectedModel(defaultModelFromSettings);
    } else if (!selectedModel && availableModels.length > 0) {
      setSelectedModel(availableModels[0]);
    }
  }, [defaultModelFromSettings, availableModels, selectedModel]);

  // Validate skill name: only allow lowercase letters, numbers, hyphens, underscores
  const validateSkillName = (value: string): string | null => {
    if (!value.trim()) {
      return 'Skill name is required';
    }
    if (!/^[a-z0-9_-]+$/.test(value)) {
      return 'Only lowercase letters, numbers, hyphens (-) and underscores (_) are allowed';
    }
    if (value.length > 50) {
      return 'Skill name must be 50 characters or less';
    }
    return null;
  };

  const handleNameChange = (value: string) => {
    // Auto-convert to lowercase and replace spaces with hyphens
    const sanitized = value.toLowerCase().replace(/\s+/g, '-');
    setName(sanitized);
    setNameError(validateSkillName(sanitized));
  };

  // Phase 2: Chat state
  const [phase, setPhase] = useState<'input' | 'chat'>('input');
  const [messages, setMessages] = useState<Array<{
    id: string;
    role: 'user' | 'assistant';
    content: ContentBlock[];
    timestamp: string;
  }>>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [inputValue, setInputValue] = useState('');
  const [isComplete, setIsComplete] = useState(false);
  const [isFinalizing, setIsFinalizing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [finalSkillName, setFinalSkillName] = useState<string | null>(null); // Sanitized skill name from backend
  const [pendingQuestion, setPendingQuestion] = useState<{
    toolUseId: string;
    questions: AskUserQuestionType[];
  } | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<(() => void) | null>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Start skill creation
  const handleStartCreation = () => {
    if (!name.trim() || !description.trim()) return;

    setPhase('chat');
    setError(null);

    // Add user's initial request as a message
    const userMessage = {
      id: Date.now().toString(),
      role: 'user' as const,
      content: [{ type: 'text' as const, text: `Create a skill named "${name}" that ${description}` }],
      timestamp: new Date().toISOString(),
    };
    setMessages([userMessage]);

    // Create assistant placeholder
    const assistantMessageId = (Date.now() + 1).toString();
    const assistantMessage = {
      id: assistantMessageId,
      role: 'assistant' as const,
      content: [] as ContentBlock[],
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, assistantMessage]);
    setIsStreaming(true);

    // Start streaming
    const abort = skillsService.streamGenerateWithAgent(
      {
        skillName: name,
        skillDescription: description,
        model: selectedModel,
      },
      (event: StreamEvent) => {
        // Handle session_start event to get session_id early for stop functionality
        if (event.type === 'session_start' && event.sessionId) {
          setSessionId(event.sessionId);
        } else if (event.type === 'assistant' && event.content) {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: [...msg.content, ...event.content!] }
                : msg
            )
          );
        } else if (event.type === 'ask_user_question' && event.questions && event.toolUseId) {
          // Store pending question for user to answer
          setPendingQuestion({
            toolUseId: event.toolUseId,
            questions: event.questions,
          });
          // Set session ID from the event if available
          if (event.sessionId) {
            setSessionId(event.sessionId);
          }
          // Add question to messages as a content block
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? {
                    ...msg,
                    content: [
                      ...msg.content,
                      {
                        type: 'ask_user_question' as const,
                        toolUseId: event.toolUseId!,
                        questions: event.questions!,
                      },
                    ],
                  }
                : msg
            )
          );
          setIsStreaming(false);
        } else if (event.type === 'result') {
          if (event.sessionId) {
            setSessionId(event.sessionId);
          }
          // Save the sanitized skill name returned by backend (note: backend returns skill_name in snake_case)
          const returnedSkillName = (event as unknown as { skill_name?: string }).skill_name || event.skillName;
          if (returnedSkillName) {
            setFinalSkillName(returnedSkillName);
          }
          setIsComplete(true);
        } else if (event.type === 'error') {
          const errorMsg = event.message || event.error || event.detail || 'An unknown error occurred';
          setError(errorMsg);
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: [{ type: 'text', text: `Error: ${errorMsg}` }] }
                : msg
            )
          );
        }
      },
      (err) => {
        console.error('Stream error:', err);
        setError(err.message);
        setIsStreaming(false);
      },
      () => {
        setIsStreaming(false);
      }
    );

    abortRef.current = abort;
  };

  // Send follow-up message for iteration
  const handleSendMessage = () => {
    if (!inputValue.trim() || isStreaming) return;

    const userMessage = {
      id: Date.now().toString(),
      role: 'user' as const,
      content: [{ type: 'text' as const, text: inputValue }],
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setIsComplete(false);
    setError(null);

    const assistantMessageId = (Date.now() + 1).toString();
    const assistantMessage = {
      id: assistantMessageId,
      role: 'assistant' as const,
      content: [] as ContentBlock[],
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, assistantMessage]);
    setIsStreaming(true);

    const abort = skillsService.streamGenerateWithAgent(
      {
        skillName: name,
        skillDescription: description,
        sessionId,
        message: inputValue,
        model: selectedModel,
      },
      (event: StreamEvent) => {
        // Handle session_start event to get session_id early for stop functionality
        if (event.type === 'session_start' && event.sessionId) {
          setSessionId(event.sessionId);
        } else if (event.type === 'assistant' && event.content) {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: [...msg.content, ...event.content!] }
                : msg
            )
          );
        } else if (event.type === 'ask_user_question' && event.questions && event.toolUseId) {
          // Store pending question for user to answer
          setPendingQuestion({
            toolUseId: event.toolUseId,
            questions: event.questions,
          });
          // Set session ID from the event if available
          if (event.sessionId) {
            setSessionId(event.sessionId);
          }
          // Add question to messages as a content block
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? {
                    ...msg,
                    content: [
                      ...msg.content,
                      {
                        type: 'ask_user_question' as const,
                        toolUseId: event.toolUseId!,
                        questions: event.questions!,
                      },
                    ],
                  }
                : msg
            )
          );
          setIsStreaming(false);
        } else if (event.type === 'result') {
          if (event.sessionId) {
            setSessionId(event.sessionId);
          }
          // Save the sanitized skill name returned by backend (note: backend returns skill_name in snake_case)
          const returnedSkillName = (event as unknown as { skill_name?: string }).skill_name || event.skillName;
          if (returnedSkillName) {
            setFinalSkillName(returnedSkillName);
          }
          setIsComplete(true);
        } else if (event.type === 'error') {
          const errorMsg = event.message || event.error || event.detail || 'An unknown error occurred';
          setError(errorMsg);
        }
      },
      (err) => {
        console.error('Stream error:', err);
        setError(err.message);
        setIsStreaming(false);
      },
      () => {
        setIsStreaming(false);
      }
    );

    abortRef.current = abort;
  };

  // Finalize and save skill
  const handleFinalize = async () => {
    // Use finalSkillName (sanitized by backend) if available, otherwise fall back to user input
    const skillNameToFinalize = finalSkillName || name;
    if (!skillNameToFinalize) {
      setError('No skill name available to finalize');
      return;
    }

    setIsFinalizing(true);
    setError(null);
    try {
      // Fetch the generated skill by folder name
      const skill = await skillsService.get(skillNameToFinalize);
      onGenerate(skill);
    } catch (err) {
      console.error('Failed to finalize skill:', err);
      setError(err instanceof Error ? err.message : 'Failed to finalize skill');
    } finally {
      setIsFinalizing(false);
    }
  };

  // Handle stop button
  const handleStop = async () => {
    if (!sessionId) return;

    try {
      // Abort the current stream if there's an abort function
      if (abortRef.current) {
        abortRef.current();
        abortRef.current = null;
      }

      // Call the backend to interrupt the session
      await chatService.stopSession(sessionId);

      // Add a system message indicating the stop
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: 'assistant' as const,
          content: [{ type: 'text' as const, text: '⏹️ Generation stopped by user.' }],
          timestamp: new Date().toISOString(),
        },
      ]);
    } catch (error) {
      console.error('Failed to stop session:', error);
    } finally {
      setIsStreaming(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  // Handle answering AskUserQuestion
  const handleAnswerQuestion = (_toolUseId: string, answers: Record<string, string>) => {
    if (!sessionId) return;

    setPendingQuestion(null);
    setIsStreaming(true);
    setError(null);

    // Create assistant message placeholder for continued response
    const assistantMessageId = Date.now().toString();
    const assistantMessage = {
      id: assistantMessageId,
      role: 'assistant' as const,
      content: [] as ContentBlock[],
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, assistantMessage]);

    // Note: For skills page, we use the skill generation API which handles AskUserQuestion internally
    // We send a follow-up message with the answers formatted as text
    const answerText = Object.entries(answers)
      .map(([question, answer]) => `${question}: ${answer}`)
      .join('\n');

    const abort = skillsService.streamGenerateWithAgent(
      {
        skillName: name,
        skillDescription: description,
        sessionId,
        message: `User answers:\n${answerText}`,
        model: selectedModel,
      },
      (event: StreamEvent) => {
        // Handle session_start event to get session_id early for stop functionality
        if (event.type === 'session_start' && event.sessionId) {
          setSessionId(event.sessionId);
        } else if (event.type === 'assistant' && event.content) {
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? { ...msg, content: [...msg.content, ...event.content!] }
                : msg
            )
          );
        } else if (event.type === 'ask_user_question' && event.questions && event.toolUseId) {
          setPendingQuestion({
            toolUseId: event.toolUseId,
            questions: event.questions,
          });
          setMessages((prev) =>
            prev.map((msg) =>
              msg.id === assistantMessageId
                ? {
                    ...msg,
                    content: [
                      ...msg.content,
                      {
                        type: 'ask_user_question' as const,
                        toolUseId: event.toolUseId!,
                        questions: event.questions!,
                      },
                    ],
                  }
                : msg
            )
          );
          setIsStreaming(false);
        } else if (event.type === 'result') {
          if (event.sessionId) {
            setSessionId(event.sessionId);
          }
          const returnedSkillName = (event as unknown as { skill_name?: string }).skill_name || event.skillName;
          if (returnedSkillName) {
            setFinalSkillName(returnedSkillName);
          }
          setIsComplete(true);
        } else if (event.type === 'error') {
          const errorMsg = event.message || event.error || event.detail || 'An unknown error occurred';
          setError(errorMsg);
        }
      },
      (err) => {
        console.error('Stream error:', err);
        setError(err.message);
        setIsStreaming(false);
      },
      () => {
        setIsStreaming(false);
      }
    );

    abortRef.current = abort;
  };

  // Phase 1: Input form
  if (phase === 'input') {
    const isNameValid = name.trim() && !nameError;

    return (
      <div className="space-y-4">
        <div>
          <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">{t('skills.create.nameLabel')}</label>
          <input
            type="text"
            value={name}
            onChange={(e) => handleNameChange(e.target.value)}
            placeholder={t('skills.create.namePlaceholder')}
            required
            className={`w-full px-4 py-2 bg-[var(--color-bg)] border rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary ${
              nameError ? 'border-status-error' : 'border-[var(--color-border)]'
            }`}
          />
          {nameError && (
            <p className="mt-1 text-sm text-status-error">{nameError}</p>
          )}
          <p className="mt-1 text-xs text-[var(--color-text-muted)]">
            {t('skills.create.nameHelp')}
          </p>
        </div>

        <div>
          <label className="block text-sm font-medium text-[var(--color-text-muted)] mb-2">{t('skills.create.descriptionLabel')}</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={t('skills.create.descriptionPlaceholder')}
            rows={5}
            required
            className="w-full px-4 py-2 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary resize-none"
          />
        </div>

        <Dropdown
          label={t('skills.create.model')}
          options={modelOptions}
          selectedId={selectedModel}
          onChange={setSelectedModel}
          placeholder={t('skills.create.selectModel')}
        />

        <div className="bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg p-4">
          <div className="flex items-start gap-3">
            <span className="material-symbols-outlined text-primary">info</span>
            <div>
              <p className="text-sm text-[var(--color-text)] font-medium">{t('skills.create.howItWorks')}</p>
              <p className="text-sm text-[var(--color-text-muted)] mt-1">
                {t('skills.create.howItWorksDesc')}
              </p>
            </div>
          </div>
        </div>

        <div className="flex gap-3 pt-4">
          <Button type="button" variant="secondary" className="flex-1" onClick={onClose}>
            {t('common.button.cancel')}
          </Button>
          <Button
            type="button"
            className="flex-1"
            onClick={handleStartCreation}
            disabled={!isNameValid || !description.trim()}
          >
            {t('skills.create.startCreating')}
          </Button>
        </div>
      </div>
    );
  }

  // Phase 2: Chat interface
  return (
    <div className="flex flex-col h-[700px]">
      {/* Chat Header */}
      <div className="flex items-center justify-between pb-4 border-b border-[var(--color-border)]">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-primary">smart_toy</span>
          <div>
            <h3 className="font-medium text-[var(--color-text)]">{t('skills.create.skillCreator')}</h3>
            <p className="text-xs text-[var(--color-text-muted)]">{t('skills.create.creating', { name })}</p>
          </div>
        </div>
        {isStreaming && (
          <span className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
            <Spinner size="sm" />
            {t('chat.thinking')}
          </span>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto py-4 space-y-4">
        {messages.map((message) => (
          <div
            key={message.id}
            className={`flex gap-3 ${message.role === 'user' ? 'flex-row-reverse' : ''}`}
          >
            <div
              className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
                message.role === 'user' ? 'bg-orange-500/20' : 'bg-[var(--color-card)]'
              }`}
            >
              <span
                className={`material-symbols-outlined text-sm ${
                  message.role === 'user' ? 'text-orange-400' : 'text-primary'
                }`}
              >
                {message.role === 'user' ? 'person' : 'smart_toy'}
              </span>
            </div>
            <div className={`flex-1 max-w-[85%] ${message.role === 'user' ? 'text-right' : ''}`}>
              <div className="space-y-2">
                {message.content.map((block, index) => (
                  <ContentBlockRenderer
                    key={index}
                    block={block}
                    onAnswerQuestion={handleAnswerQuestion}
                    pendingToolUseId={pendingQuestion?.toolUseId}
                    isStreaming={isStreaming}
                  />
                ))}
              </div>
            </div>
          </div>
        ))}
        {isStreaming && messages[messages.length - 1]?.content.length === 0 && (
          <div className="flex items-center gap-2 text-[var(--color-text-muted)]">
            <Spinner size="sm" />
            <span className="text-sm">Agent is working...</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Error display */}
      {error && (
        <div className="mb-4 p-3 bg-status-error/10 border border-status-error/30 rounded-lg">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-status-error">error</span>
            <span className="text-status-error text-sm">{error}</span>
          </div>
        </div>
      )}

      {/* Input Area */}
      <div className="pt-4 border-t border-[var(--color-border)]">
        {isComplete && !isStreaming ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2 p-3 bg-status-success/10 border border-status-success/30 rounded-lg">
              <span className="material-symbols-outlined text-status-success">check_circle</span>
              <span className="text-status-success text-sm">Skill creation complete! You can iterate or save.</span>
            </div>
            <div className="flex gap-3">
              <div className="relative flex-1">
                <input
                  type="text"
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={isStreaming ? 'Generating...' : 'Request changes or improvements...'}
                  disabled={isStreaming}
                  className="w-full px-4 py-2 pr-10 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary disabled:opacity-50"
                />
                <button
                  onClick={isStreaming ? handleStop : handleSendMessage}
                  disabled={!isStreaming && !inputValue.trim()}
                  className={`absolute right-2 top-1/2 -translate-y-1/2 p-1 ${
                    isStreaming ? 'bg-red-500 hover:bg-red-600' : 'bg-primary hover:bg-primary-hover'
                  } disabled:opacity-50 disabled:cursor-not-allowed rounded transition-colors`}
                  title={isStreaming ? 'Stop generation' : 'Send message'}
                >
                  <span className="material-symbols-outlined text-white text-sm">
                    {isStreaming ? 'stop' : 'send'}
                  </span>
                </button>
              </div>
              <Button onClick={handleFinalize} isLoading={isFinalizing} disabled={isFinalizing}>
                {isFinalizing ? t('skills.create.saving') : t('skills.create.saveSkill')}
              </Button>
            </div>
          </div>
        ) : (
          <div className="relative">
            <input
              type="text"
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={isStreaming ? 'Generating...' : 'Send a message...'}
              disabled={isStreaming}
              className="w-full px-4 py-2 pr-10 bg-[var(--color-bg)] border border-[var(--color-border)] rounded-lg text-[var(--color-text)] placeholder:text-[var(--color-text-muted)] focus:outline-none focus:border-primary disabled:opacity-50"
            />
            <button
              onClick={isStreaming ? handleStop : handleSendMessage}
              disabled={!isStreaming && !inputValue.trim()}
              className={`absolute right-2 top-1/2 -translate-y-1/2 p-1 ${
                isStreaming ? 'bg-red-500 hover:bg-red-600' : 'bg-primary hover:bg-primary-hover'
              } disabled:opacity-50 disabled:cursor-not-allowed rounded transition-colors`}
              title={isStreaming ? 'Stop generation' : 'Send message'}
            >
              <span className="material-symbols-outlined text-white text-sm">
                {isStreaming ? 'stop' : 'send'}
              </span>
            </button>
          </div>
        )}
      </div>

      {/* Cancel button */}
      <div className="flex justify-end pt-4">
        <Button variant="secondary" onClick={onClose} disabled={isStreaming || isFinalizing}>
          {isComplete ? 'Close' : 'Cancel'}
        </Button>
      </div>
    </div>
  );
}

// Content Block Renderer for chat messages
interface ContentBlockRendererProps {
  block: ContentBlock;
  onAnswerQuestion?: (toolUseId: string, answers: Record<string, string>) => void;
  pendingToolUseId?: string;
  isStreaming?: boolean;
}

function ContentBlockRenderer({ block, onAnswerQuestion, pendingToolUseId, isStreaming }: ContentBlockRendererProps) {
  if (block.type === 'text') {
    return <MarkdownRenderer content={block.text || ''} className="text-sm" />;
  }

  if (block.type === 'tool_use') {
    return (
      <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg overflow-hidden text-sm">
        <div className="flex items-center justify-between px-3 py-1.5 bg-[var(--color-hover)]">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-primary text-sm">terminal</span>
            <span className="font-medium text-[var(--color-text)]">{block.name}</span>
          </div>
        </div>
        <div className="p-3 max-h-32 overflow-y-auto">
          <pre className="text-xs text-[var(--color-text-muted)] overflow-x-auto whitespace-pre-wrap break-words">
            <code>{block.summary}</code>
          </pre>
        </div>
      </div>
    );
  }

  if (block.type === 'tool_result') {
    return (
      <div className="bg-[var(--color-card)] border border-[var(--color-border)] rounded-lg p-3 text-sm">
        <div className="flex items-center gap-2 mb-1">
          <span className="material-symbols-outlined text-status-success text-sm">check_circle</span>
          <span className="font-medium text-[var(--color-text)]">Result</span>
        </div>
        <pre className="text-xs text-[var(--color-text-muted)] overflow-x-auto whitespace-pre-wrap break-words max-h-24 overflow-y-auto">
          <code>{block.content}</code>
        </pre>
      </div>
    );
  }

  if (block.type === 'ask_user_question') {
    const isPending = pendingToolUseId === block.toolUseId;
    const isAnswered = !isPending && !isStreaming;

    return (
      <AskUserQuestion
        questions={block.questions}
        toolUseId={block.toolUseId}
        onSubmit={onAnswerQuestion || (() => {})}
        disabled={isAnswered || isStreaming}
      />
    );
  }

  return null;
}
