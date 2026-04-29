/**
 * Tests for fileViewTypes — file classification for unified FileViewer.
 * Covers all FileViewType categories and edge cases.
 */
import { describe, it, expect } from 'vitest';
import {
  classifyFileForViewer,
  isEditableType,
  isBinaryType,
  getFileTypeInfo,
  type FileViewType,
} from './fileViewTypes';

describe('classifyFileForViewer', () => {
  // AC1: Images
  it.each([
    ['photo.png', 'image'],
    ['avatar.jpg', 'image'],
    ['icon.jpeg', 'image'],
    ['animation.gif', 'image'],
    ['modern.webp', 'image'],
    ['legacy.bmp', 'image'],
    ['favicon.ico', 'image'],
    ['next-gen.avif', 'image'],
  ] satisfies [string, FileViewType][])(
    'classifies %s as %s', (fileName, expected) => {
      expect(classifyFileForViewer(fileName)).toBe(expected);
    }
  );

  // AC2: PDF
  it('classifies PDF files', () => {
    expect(classifyFileForViewer('report.pdf')).toBe('pdf');
    expect(classifyFileForViewer('DOCUMENT.PDF')).toBe('pdf'); // uppercase extension
  });

  // AC3: HTML
  it('classifies HTML files for preview', () => {
    expect(classifyFileForViewer('page.html')).toBe('html-preview');
    expect(classifyFileForViewer('old-page.htm')).toBe('html-preview');
  });

  // AC4: Video & Audio
  it.each([
    ['clip.mp4', 'video'],
    ['demo.webm', 'video'],
    ['screen.mov', 'video'],
    ['song.mp3', 'audio'],
    ['voice.wav', 'audio'],
    ['podcast.ogg', 'audio'],
    ['music.m4a', 'audio'],
    ['lossless.flac', 'audio'],
  ] satisfies [string, FileViewType][])(
    'classifies %s as %s', (fileName, expected) => {
      expect(classifyFileForViewer(fileName)).toBe(expected);
    }
  );

  // AC5: CSV
  it('classifies CSV/TSV files', () => {
    expect(classifyFileForViewer('data.csv')).toBe('csv');
    expect(classifyFileForViewer('table.tsv')).toBe('csv');
  });

  // AC6: Unsupported (Office, archives, executables, etc.)
  it.each([
    ['report.docx', 'unsupported'],
    ['budget.xlsx', 'unsupported'],
    ['slides.pptx', 'unsupported'],
    ['archive.zip', 'unsupported'],
    ['backup.tar', 'unsupported'],
    ['app.exe', 'unsupported'],
    ['lib.dll', 'unsupported'],
    ['data.sqlite', 'unsupported'],
    ['font.ttf', 'unsupported'],
    ['photo.heic', 'unsupported'],
    ['raw.tiff', 'unsupported'],
  ] satisfies [string, FileViewType][])(
    'classifies %s as unsupported', (fileName, expected) => {
      expect(classifyFileForViewer(fileName)).toBe(expected);
    }
  );

  // Text/code files
  it.each([
    ['app.tsx', 'text'],
    ['server.py', 'text'],
    ['main.rs', 'text'],
    ['config.json', 'text'],
    ['schema.yaml', 'text'],
    ['style.css', 'text'],
    ['.env', 'text'],
    ['Makefile', 'text'], // no extension → text
    ['Dockerfile', 'text'],
  ] satisfies [string, FileViewType][])(
    'classifies %s as text', (fileName, expected) => {
      expect(classifyFileForViewer(fileName)).toBe(expected);
    }
  );

  // Markdown
  it.each([
    ['README.md', 'markdown'],
    ['notes.markdown', 'markdown'],
    ['docs.mdx', 'markdown'],
  ] satisfies [string, FileViewType][])(
    'classifies %s as markdown', (fileName, expected) => {
      expect(classifyFileForViewer(fileName)).toBe(expected);
    }
  );

  // SVG
  it('classifies SVG as its own type (editable + previewable)', () => {
    expect(classifyFileForViewer('logo.svg')).toBe('svg');
  });

  // Edge cases
  it('handles files without extension as text', () => {
    expect(classifyFileForViewer('README')).toBe('text');
    expect(classifyFileForViewer('.gitignore')).toBe('text');
  });

  it('handles uppercase extensions', () => {
    expect(classifyFileForViewer('PHOTO.PNG')).toBe('image');
    expect(classifyFileForViewer('REPORT.PDF')).toBe('pdf');
  });
});

describe('isEditableType', () => {
  it('marks text-based types as editable', () => {
    expect(isEditableType('text')).toBe(true);
    expect(isEditableType('markdown')).toBe(true);
    expect(isEditableType('svg')).toBe(true);
    expect(isEditableType('csv')).toBe(true);
  });

  it('marks non-text types as non-editable', () => {
    expect(isEditableType('image')).toBe(false);
    expect(isEditableType('pdf')).toBe(false);
    expect(isEditableType('video')).toBe(false);
    expect(isEditableType('audio')).toBe(false);
    expect(isEditableType('html-preview')).toBe(false);
    expect(isEditableType('unsupported')).toBe(false);
  });
});

describe('isBinaryType', () => {
  it('identifies binary types correctly', () => {
    expect(isBinaryType('image')).toBe(true);
    expect(isBinaryType('pdf')).toBe(true);
    expect(isBinaryType('video')).toBe(true);
    expect(isBinaryType('audio')).toBe(true);
    expect(isBinaryType('unsupported')).toBe(true);
  });

  it('identifies text types correctly', () => {
    expect(isBinaryType('text')).toBe(false);
    expect(isBinaryType('markdown')).toBe(false);
    expect(isBinaryType('svg')).toBe(false);
    expect(isBinaryType('csv')).toBe(false);
    expect(isBinaryType('html-preview')).toBe(false);
  });
});

describe('getFileTypeInfo', () => {
  it('returns correct info for common types', () => {
    const png = getFileTypeInfo('photo.png');
    expect(png.label).toBe('PNG Image');
    expect(png.icon).toBe('image');
    expect(png.category).toBe('media');

    const pdf = getFileTypeInfo('report.pdf');
    expect(pdf.label).toBe('PDF Document');
    expect(pdf.icon).toBe('picture_as_pdf');
    expect(pdf.category).toBe('document');

    const py = getFileTypeInfo('main.py');
    expect(py.label).toBe('Python');
    expect(py.icon).toBe('code');
    expect(py.category).toBe('text');

    const md = getFileTypeInfo('README.md');
    expect(md.label).toBe('Markdown');

    const csv = getFileTypeInfo('data.csv');
    expect(csv.label).toBe('CSV Spreadsheet');
    expect(csv.icon).toBe('table_chart');
    expect(csv.category).toBe('data');
  });

  it('returns correct info for unsupported types', () => {
    const docx = getFileTypeInfo('report.docx');
    expect(docx.label).toBe('Word Document');
    expect(docx.icon).toBe('article');
    expect(docx.category).toBe('binary');

    const zip = getFileTypeInfo('archive.zip');
    expect(zip.label).toBe('ZIP Archive');
    expect(zip.icon).toBe('folder_zip');
  });
});
