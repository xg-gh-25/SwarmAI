/**
 * The no-hardcoded-ui-text lint rule (eslint-rules/no-hardcoded-ui-text.js) and its
 * wiring into the CI lint (eslint.contract.config.js, run by `npm run lint:contract`).
 *
 * What it must catch: words a person reads that are written into the code instead
 * of going through i18n - JSX text, and string / template literals in the title,
 * placeholder, aria-label, label and alt attributes, in any script (Latin or CJK);
 * in .tsx and .ts files alike, string values of user-facing object keys (label,
 * title, message, navLabel, ..., emptyMessage, ariaLabel) and the first argument of
 * the calls that show text (setError, set<Name>Error, alert, confirm, toast,
 * toast.<x>, addToast, showToast).
 * What it must leave alone: t() calls, text without letters, the reviewed exceptions
 * (product names, key caps, URLs, code, region codes, log labels), icon ligature
 * names, *Key properties, literals that are locale keys, and test files.
 */
import { describe, it, expect } from 'vitest';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ESLint, RuleTester } from 'eslint';
import tseslint from 'typescript-eslint';
import rule from '../../eslint-rules/no-hardcoded-ui-text.js';

RuleTester.describe = describe;
RuleTester.it = it;
RuleTester.itOnly = it.only;

const tester = new RuleTester({
  languageOptions: {
    parser: tseslint.parser,
    ecmaVersion: 2020,
    sourceType: 'module',
    parserOptions: { ecmaFeatures: { jsx: true } },
  },
});

const file = 'src/components/Example.tsx';
const tsFile = 'src/components/layout/exampleTable.ts';
const err = (text: string) => ({ messageId: 'hardcoded' as const, data: { text } });

tester.run('no-hardcoded-ui-text', rule, {
  valid: [
    { filename: file, code: "const a = <p>{t('chat.send')}</p>;" },
    { filename: file, code: "const a = <button title={t('chat.send')} aria-label={t('chat.send')} />;" },
    { filename: file, code: 'const a = <span>{count} · {total}</span>;' },
    { filename: file, code: 'const a = <span>→</span>;' },
    { filename: file, code: 'const a = <span>  </span>;' },
    { filename: file, code: 'const a = <span>{modKeyLabel()}↩</span>;' },
    // Reviewed exceptions: product names, key caps, code.
    { filename: file, code: 'const a = <h1>SwarmAI</h1>;' },
    { filename: file, code: 'const a = <span>Slack</span>;' },
    { filename: file, code: 'const a = <span>Esc</span>;' },
    { filename: file, code: 'const a = <span>K</span>;' },
    { filename: file, code: 'const a = <code>xcode-select --install</code>;' },
    { filename: file, code: "const a = <input placeholder='https://github.com/user/repo.git' />;" },
    // Icon ligature names are not words a person reads.
    { filename: file, code: 'const a = <span className="material-symbols-outlined icon-xs">check_circle</span>;' },
    { filename: file, code: "const a = <span className={`material-symbols-outlined ${c}`}>{open ? 'expand_less' : 'expand_more'}</span>;" },
    { filename: file, code: "const a = <span className={clsx('material-symbols-outlined', open && 'rotate-180')}>expand_more</span>;" },
    { filename: file, code: 'const a = <span className="font-mono">aim.json</span>;' },
    { filename: file, code: 'const a = <span>{secs}s</span>;' },
    { filename: file, code: 'const a = <span>v{version}</span>;' },
    // Attributes that are not read by a person.
    { filename: file, code: 'const a = <div className="flex gap-2" data-testid="send-button" />;' },
    // t() with an English default is fine: the key is what ships.
    { filename: file, code: "const a = <button title={t('chat.newSession', 'New Session')} />;" },
    // Object properties: keys a person does not read, *Key holders, locale keys.
    { filename: tsFile, code: "export const A = [{ id: 'history', icon: 'history', kind: 'overlay' }];" },
    { filename: tsFile, code: "export const A = [{ labelKey: 'ui.navTable.history', subtitleKey: 'ui.navTable.historySub' }];" },
    { filename: tsFile, code: "export const A = { titleKey: 'Not a key but a holder' };" },
    { filename: tsFile, code: "export const A = { label: 'common.button.cancel' };" },
    { filename: tsFile, code: "export const A = { message: t('ui.api.networkError') };" },
    { filename: tsFile, code: "export const A = { label: '⌘K', hint: '⇧D', title: '→' };" },
    { filename: tsFile, code: "export const A = { label: 'us-east-1', detail: 'ap-southeast-2' };" },
    { filename: tsFile, code: "export const A = { label: 'HTTP', title: 'SwarmAI' };" },
    { filename: tsFile, code: "export const A = { [dynamic]: 'computed keys are not read' };" },
    { filename: tsFile, code: "export const A = { labels: 'plural is another word', messageId: 'an id' };" },
    { filename: 'src/services/chat.ts', code: "stream({ label: 'streamChat' }); stream({ label: 'answer-question' }); stream({ label: 'cmd-permission-continue' });" },
    { filename: 'src/pages/SkillsPage.tsx', code: 'send({ message: `User answers: ${answers}` });' },
    { filename: tsFile, code: "export const A = { scopeLabel: 'Workspace: SwarmWS (General)' };" },
    // Calls: other callees, t() inside, no letters.
    { filename: tsFile, code: "console.error('Failed to load'); logger.warn('Retrying');" },
    { filename: tsFile, code: "setError(t('ui.x.failed')); setError(null); setError(err.message);" },
    { filename: tsFile, code: "setLoadError(i18next.t('ui.x.failed')); toast.error(t('ui.x.failed'));" },
    { filename: tsFile, code: "setErrorCode('E_NOT_FOUND'); setValue('Hello there');" },
    { filename: tsFile, code: "setError('');" },
    // Test files stay out for .ts too.
    { filename: 'src/components/layout/exampleTable.test.ts', code: "export const A = { label: 'Hello world' };" },
    // Test and story files are not shipped UI.
    { filename: 'src/components/__tests__/Example.tsx', code: 'const a = <p>Hello world</p>;' },
    { filename: 'src/components/Example.test.tsx', code: 'const a = <p>Hello world</p>;' },
    { filename: 'src/components/Example.stories.tsx', code: 'const a = <p>Hello world</p>;' },
  ],
  invalid: [
    { filename: file, code: 'const a = <p>Hello world</p>;', errors: [err('Hello world')] },
    { filename: file, code: 'const a = <p>你好</p>;', errors: [err('你好')] },
    { filename: file, code: 'const a = <p>\n   Verifying your environment...\n</p>;', errors: [err('Verifying your environment...')] },
    { filename: file, code: "const a = <button title='Close panel' />;", errors: [err('Close panel')] },
    { filename: file, code: 'const a = <input placeholder="Search files" />;', errors: [err('Search files')] },
    { filename: file, code: "const a = <button aria-label={'Close'} />;", errors: [err('Close')] },
    { filename: file, code: 'const a = <img alt="Logo of the app" />;', errors: [err('Logo of the app')] },
    { filename: file, code: 'const a = <Field label="Repository URL" />;', errors: [err('Repository URL')] },
    { filename: file, code: 'const a = <button title={`Open file (⌘O)`} />;', errors: [err('Open file (⌘O)')] },
    {
      filename: file,
      code: 'const a = <button title={`Open the terminal in ${where}`} />;',
      errors: [err('Open the terminal in ${…}')],
    },
    {
      filename: file,
      code: "const a = <button aria-label={open ? 'Collapse input' : 'Expand input'} />;",
      errors: [err('Collapse input'), err('Expand input')],
    },
    {
      filename: file,
      code: "const a = <button>{showSlack ? 'Cancel' : 'Set Up'}</button>;",
      errors: [err('Cancel'), err('Set Up')],
    },
    {
      filename: file,
      code: "const a = <p>{isDir && ' This includes all contents.'}</p>;",
      errors: [err('This includes all contents.')],
    },
    // Object properties whose key a person reads, in .ts and .tsx.
    { filename: tsFile, code: "export const A = [{ id: 'history', navLabel: 'History', subtitle: 'Past sessions' }];", errors: [err('History'), err('Past sessions')] },
    { filename: file, code: "const items = [{ label: 'Move to Trash', icon: 'delete' }];", errors: [err('Move to Trash')] },
    { filename: tsFile, code: "export const A = { title: 'Brain', desc: 'Grow it', description: 'Long', detail: 'More', placeholder: 'Type', tooltip: 'Tip', hint: 'Hint', message: 'Oops' };",
      errors: ['Brain', 'Grow it', 'Long', 'More', 'Type', 'Tip', 'Hint', 'Oops'].map(err) },
    { filename: tsFile, code: "export const A = { emptyMessage: 'Nothing here', ariaLabel: 'Close panel', errorDetail: 'Disk full' };", errors: [err('Nothing here'), err('Close panel'), err('Disk full')] },
    { filename: tsFile, code: "export const A = { 'label': 'Quoted key' };", errors: [err('Quoted key')] },
    { filename: tsFile, code: "export const A = { message: ok ? 'Saved' : `Failed: ${why}` };", errors: [err('Saved'), err('Failed: ${…}')] },
    { filename: tsFile, code: "export const A = { label: 'Recent' as const };", errors: [err('Recent')] },
    { filename: tsFile, code: "export const A = { label: '你好' };", errors: [err('你好')] },
    // A text that looks like a key but is not one in en.json is still text.
    { filename: tsFile, code: "export const A = { label: 'ui.navTable.noSuchKeyEver' };", errors: [err('ui.navTable.noSuchKeyEver')] },
    // Calls that show their first argument to a person.
    { filename: tsFile, code: "setError('Upload failed');", errors: [err('Upload failed')] },
    { filename: tsFile, code: "setLoadError(`Failed to load ${name}`);", errors: [err('Failed to load ${…}')] },
    { filename: tsFile, code: "setErrorMessage(e.message || 'Unknown error');", errors: [err('Unknown error')] },
    { filename: file, code: "alert('PNG download failed'); window.confirm('Delete it?');", errors: [err('PNG download failed'), err('Delete it?')] },
    { filename: tsFile, code: "toast('Saved'); toast.error('Not saved'); addToast('Copied'); showToast('Moved');", errors: ['Saved', 'Not saved', 'Copied', 'Moved'].map(err) },
    { filename: tsFile, code: "toastCtx?.addToast({ severity: 'error', message: `Rename failed: ${e}` });", errors: [err('Rename failed: ${…}')] },
    { filename: tsFile, code: "this.setError('Stopped');", errors: [err('Stopped')] },
    // A product name inside a sentence is still a sentence.
    { filename: file, code: 'const a = <p>Start Using SwarmAI</p>;', errors: [err('Start Using SwarmAI')] },
  ],
});

describe('CI wiring', () => {
  it('eslint.contract.config.js (npm run lint:contract) runs the rule on every component', async () => {
    const desktop = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
    const eslint = new ESLint({ cwd: desktop, overrideConfigFile: join(desktop, 'eslint.contract.config.js') });
    const config = await eslint.calculateConfigForFile(join(desktop, 'src/components/settings/GeneralTab.tsx'));
    expect(config.rules['local/no-hardcoded-ui-text']).toEqual([2]);
  });

  it('reports a hardcoded string in a real component path through the CI config', async () => {
    const desktop = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
    const eslint = new ESLint({ cwd: desktop, overrideConfigFile: join(desktop, 'eslint.contract.config.js') });
    const [res] = await eslint.lintText('export const A = () => <p>Hardcoded words</p>;\n', {
      filePath: join(desktop, 'src/components/Probe.tsx'),
    });
    expect(res.messages.map((m) => m.ruleId)).toContain('local/no-hardcoded-ui-text');
  });

  it('runs the rule on .ts files under src (data tables, hooks, services)', async () => {
    const desktop = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
    const eslint = new ESLint({ cwd: desktop, overrideConfigFile: join(desktop, 'eslint.contract.config.js') });
    const config = await eslint.calculateConfigForFile(join(desktop, 'src/components/layout/navTable.ts'));
    expect(config.rules['local/no-hardcoded-ui-text']).toEqual([2]);
    const [res] = await eslint.lintText("export const A = [{ navLabel: 'History' }];\n", {
      filePath: join(desktop, 'src/components/layout/probeTable.ts'),
    });
    expect(res.messages.map((m) => m.ruleId)).toContain('local/no-hardcoded-ui-text');
  });

  it('the files the extended rule first found now lint clean (migrated to i18n keys)', async () => {
    const desktop = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
    const eslint = new ESLint({ cwd: desktop, overrideConfigFile: join(desktop, 'eslint.contract.config.js') });
    const files = [
      'src/components/layout/navTable.ts', 'src/components/layout/brainMapModel.ts',
      'src/components/workspace-explorer/FileContextMenu.tsx', 'src/components/common/BackendStartupOverlay.tsx',
      'src/components/layout/LibraryOverlay.tsx', 'src/components/layout/ToDoOverlay.tsx',
      'src/components/settings/hiveComponents.tsx', 'src/services/hive.ts', 'src/services/api.ts',
      'src/hooks/useUnifiedAttachments.ts', 'src/hooks/backendIssue.ts',
    ].map((f) => join(desktop, f));
    const results = await eslint.lintFiles(files);
    expect(results).toHaveLength(files.length);
    const problems = results.flatMap((r) => r.messages.filter((m) => m.severity === 2)
      .map((m) => `${r.filePath.slice(desktop.length + 1)}:${m.line}: ${m.message.slice(0, 80)}`));
    expect(problems).toEqual([]);
  }, 60_000);
});
