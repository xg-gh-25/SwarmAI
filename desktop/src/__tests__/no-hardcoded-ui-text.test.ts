/**
 * The no-hardcoded-ui-text lint rule (eslint-rules/no-hardcoded-ui-text.js) and its
 * wiring into the CI lint (eslint.contract.config.js, run by `npm run lint:contract`).
 *
 * What it must catch: words a person reads that are written into a component instead
 * of going through i18n - JSX text, and string / template literals in the title,
 * placeholder, aria-label, label and alt attributes, in any script (Latin or CJK).
 * What it must leave alone: t() calls, text without letters, the reviewed exceptions
 * (product names, key caps, URLs, code), icon ligature names, and test files.
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
});
