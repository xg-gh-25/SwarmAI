#!/usr/bin/env node

/**
 * Generate update.json manifest for Tauri updater
 *
 * Usage:
 *   node generate-update-manifest.js \
 *     --version 0.1.0 \
 *     --notes "Release notes here" \
 *     --base-url https://cdn.example.com/releases/v0.1.0 \
 *     --macos-aarch64-sig ./Owork.app.tar.gz.sig \
 *     --macos-aarch64-file Owork_0.1.0_aarch64.app.tar.gz \
 *     --windows-x64-sig ./Owork_0.1.0_x64-setup.nsis.zip.sig \
 *     --windows-x64-file Owork_0.1.0_x64-setup.nsis.zip \
 *     --output ./update.json
 */

const fs = require('fs');
const path = require('path');

function parseArgs(args) {
  const result = {};
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg.startsWith('--')) {
      const key = arg.slice(2);
      const value = args[i + 1];
      if (value && !value.startsWith('--')) {
        result[key] = value;
        i++;
      } else {
        result[key] = true;
      }
    }
  }
  return result;
}

function readSignature(sigPath) {
  if (!sigPath || !fs.existsSync(sigPath)) {
    return null;
  }
  return fs.readFileSync(sigPath, 'utf-8').trim();
}

function main() {
  const args = parseArgs(process.argv.slice(2));

  const version = args['version'];
  const notes = args['notes'] || '';
  const baseUrl = args['base-url'];
  const output = args['output'] || 'update.json';

  if (!version || !baseUrl) {
    console.error('Usage: generate-update-manifest.js --version <ver> --base-url <url> [options]');
    console.error('Options:');
    console.error('  --notes <text>              Release notes');
    console.error('  --macos-aarch64-sig <path>  Path to macOS ARM64 signature file');
    console.error('  --macos-aarch64-file <name> macOS ARM64 artifact filename');
    console.error('  --macos-x64-sig <path>      Path to macOS x64 signature file');
    console.error('  --macos-x64-file <name>     macOS x64 artifact filename');
    console.error('  --windows-x64-sig <path>    Path to Windows x64 signature file');
    console.error('  --windows-x64-file <name>   Windows x64 artifact filename');
    console.error('  --output <path>             Output file path (default: update.json)');
    process.exit(1);
  }

  const manifest = {
    version: version,
    notes: notes,
    pub_date: new Date().toISOString(),
    platforms: {},
  };

  // macOS ARM64 (Apple Silicon)
  const macosAarch64Sig = readSignature(args['macos-aarch64-sig']);
  const macosAarch64File = args['macos-aarch64-file'];
  if (macosAarch64Sig && macosAarch64File) {
    manifest.platforms['darwin-aarch64'] = {
      signature: macosAarch64Sig,
      url: `${baseUrl}/${macosAarch64File}`,
    };
  }

  // macOS x64 (Intel)
  const macosX64Sig = readSignature(args['macos-x64-sig']);
  const macosX64File = args['macos-x64-file'];
  if (macosX64Sig && macosX64File) {
    manifest.platforms['darwin-x86_64'] = {
      signature: macosX64Sig,
      url: `${baseUrl}/${macosX64File}`,
    };
  }

  // Windows x64
  const windowsX64Sig = readSignature(args['windows-x64-sig']);
  const windowsX64File = args['windows-x64-file'];
  if (windowsX64Sig && windowsX64File) {
    manifest.platforms['windows-x86_64'] = {
      signature: windowsX64Sig,
      url: `${baseUrl}/${windowsX64File}`,
    };
  }

  if (Object.keys(manifest.platforms).length === 0) {
    console.error('Error: No platforms configured. At least one platform signature and file must be provided.');
    process.exit(1);
  }

  const jsonContent = JSON.stringify(manifest, null, 2);
  fs.writeFileSync(output, jsonContent);

  console.log(`Generated ${output}:`);
  console.log(jsonContent);
}

main();
