/**
 * Build Swarm's Birthday poster — generates HTML with embedded conversation,
 * then uses Playwright to produce PDF and long-image PNG.
 */
import { readFileSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// 1. Parse the markdown transcript into structured conversation
function parseTranscript(md) {
  const lines = md.split('\n');
  const conversation = [];
  let currentRole = null;
  let currentText = [];
  let frontmatterCount = 0; // Track opening/closing ---
  let pastFrontmatter = false;

  for (const line of lines) {
    // Handle YAML frontmatter (first two --- lines only)
    if (line.trim() === '---' && frontmatterCount < 2) {
      frontmatterCount++;
      if (frontmatterCount === 2) pastFrontmatter = true;
      continue;
    }
    if (!pastFrontmatter) continue;

    // Detect role headers
    const xgMatch = line.match(/^## \u{1F9D1} XG$/u);
    const swarmMatch = line.match(/^## \u{1F41D} Swarm$/u);
    const selfDiagMatch = line.match(/^## \u{1F41D} .+$/u);

    if (xgMatch || swarmMatch || selfDiagMatch) {
      // Save previous
      if (currentRole && currentText.length > 0) {
        const text = currentText.join('\n').trim();
        if (text) conversation.push([currentRole, text]);
      }
      currentRole = xgMatch ? 'user' : 'assistant';
      currentText = [];
      // For self-diagnosis header, include it as part of the text
      if (selfDiagMatch && !swarmMatch) {
        currentText.push(line.replace(/^## \u{1F41D} /u, '### '));
      }
      continue;
    }

    // System notes
    if (line.startsWith('*[Context compaction')) {
      if (currentRole && currentText.length > 0) {
        const text = currentText.join('\n').trim();
        if (text) conversation.push([currentRole, text]);
        currentText = [];
      }
      conversation.push(['system', 'Context compaction \u2014 conversation continued with summary of prior context']);
      continue;
    }

    if (currentRole !== null) {
      currentText.push(line);
    }
  }

  // Save last
  if (currentRole && currentText.length > 0) {
    const text = currentText.join('\n').trim();
    if (text) conversation.push([currentRole, text]);
  }

  // Merge consecutive same-role
  const merged = [];
  for (const [role, text] of conversation) {
    if (merged.length > 0 && merged[merged.length - 1][0] === role) {
      merged[merged.length - 1][1] += '\n\n' + text;
    } else {
      merged.push([role, text]);
    }
  }

  return merged;
}

// 2. Build final HTML
function buildHtml(conversation) {
  const template = readFileSync(join(__dirname, '2026-03-14-swarm-birthday-poster.html'), 'utf-8');
  const json = JSON.stringify(conversation);
  return template.replace('CONVERSATION_PLACEHOLDER', json);
}

// 3. Generate PDF and PNG via Playwright
async function generateOutputs(htmlPath) {
  const { chromium } = await import('/Users/gawan/.npm/_npx/e41f203b7505f1fb/node_modules/playwright/index.mjs');

  const browser = await chromium.launch();
  const page = await browser.newPage();

  await page.goto(`file://${htmlPath}`, { waitUntil: 'networkidle', timeout: 30000 });

  // Wait for fonts and rendering
  await page.waitForTimeout(2000);

  // PDF
  const pdfPath = join(__dirname, '2026-03-14-swarm-birthday-session.pdf');
  await page.pdf({
    path: pdfPath,
    format: 'A4',
    printBackground: true,
    margin: { top: '20px', bottom: '20px', left: '20px', right: '20px' },
    displayHeaderFooter: false,
  });
  console.log(`PDF saved: ${pdfPath}`);

  // Long image — set viewport width, measure full height
  await page.setViewportSize({ width: 800, height: 600 });
  await page.waitForTimeout(500);

  const bodyHeight = await page.evaluate(() => document.body.scrollHeight);
  await page.setViewportSize({ width: 800, height: bodyHeight });
  await page.waitForTimeout(1000);

  const pngPath = join(__dirname, '2026-03-14-swarm-birthday-poster.png');
  await page.screenshot({
    path: pngPath,
    fullPage: true,
    type: 'png',
  });
  console.log(`PNG saved: ${pngPath} (${bodyHeight}px tall)`);

  await browser.close();
  return { pdfPath, pngPath };
}

// Main
async function main() {
  console.log('Parsing transcript...');
  const md = readFileSync(join(__dirname, '2026-03-14-swarm-birthday-session.md'), 'utf-8');
  const conversation = parseTranscript(md);
  console.log(`Parsed ${conversation.length} conversation turns`);

  console.log('Building HTML...');
  const html = buildHtml(conversation);
  const htmlPath = join(__dirname, '2026-03-14-swarm-birthday-final.html');
  writeFileSync(htmlPath, html, 'utf-8');
  console.log(`HTML saved: ${htmlPath}`);

  console.log('Generating PDF and PNG...');
  const { pdfPath, pngPath } = await generateOutputs(htmlPath);

  console.log('\nDone! Generated:');
  console.log(`  HTML: ${htmlPath}`);
  console.log(`  PDF:  ${pdfPath}`);
  console.log(`  PNG:  ${pngPath}`);
}

main().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
