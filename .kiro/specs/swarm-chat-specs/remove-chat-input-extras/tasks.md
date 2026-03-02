# Implementation Plan: Remove Chat Input Extras

## Overview

Remove visual clutter from ChatInput (plugin/skill/MCP indicators, branding footer), strip unused props and components, implement auto-grow textarea, and update tests. All changes are frontend-only in `desktop/src/`. Backend and shared services remain untouched.

## Tasks

- [x] 1. Remove Plugin_Props and ReadOnlyChips from ChatInput
  - [x] 1.1 Remove Plugin_Props from ChatInputProps interface and component body
    - Remove `agentSkills`, `agentMCPs`, `agentPlugins`, `isLoadingSkills`, `isLoadingMCPs`, `isLoadingPlugins`, `allowAllSkills` from the `ChatInputProps` interface
    - Remove destructuring of these props in the component function signature
    - Remove `ReadOnlyChips` import from `'../../../components/common'`
    - Remove `Skill`, `MCPServer`, `Plugin` type imports if no other usage remains
    - _Requirements: 2.1_

  - [x] 1.2 Remove ReadOnlyChips usage from ChatInput bottom row
    - Remove the three `<ReadOnlyChips>` instances (Plugins, Skills, MCPs) from the bottom row JSX
    - Keep the slash-command hint ("Type / for commands") and its wrapper div/border-top styling
    - _Requirements: 1.1, 1.2, 1.3_

  - [x] 1.3 Remove branding footer from ChatInput
    - Remove the `<p>` element rendering "IMMERSIVE WORKSPACE • POWERED BY CLAUDE CODE" below the input box
    - Ensure no replacement text is added and layout/spacing of remaining elements is preserved
    - _Requirements: 5.1, 5.2, 5.3_

  - [x] 1.4 Remove Plugin_Props from ChatPage JSX invocation
    - Remove `agentSkills`, `agentMCPs`, `agentPlugins`, `isLoadingSkills`, `isLoadingMCPs`, `isLoadingPlugins`, `allowAllSkills` props from the `<ChatInput>` JSX in ChatPage.tsx
    - Verify that `agentSkills`, `agentMCPs`, `agentPlugins`, `enableSkills`, `enableMCP` variables remain in ChatPage for use in `handleSendMessage`
    - _Requirements: 2.2, 2.3, 6.3_

- [x] 2. Delete ReadOnlyChips component and clean up exports
  - [x] 2.1 Delete ReadOnlyChips.tsx file
    - Delete `desktop/src/components/common/ReadOnlyChips.tsx`
    - _Requirements: 3.1_

  - [x] 2.2 Remove ReadOnlyChips and ChipItem exports from common/index.ts
    - Remove `export { default as ReadOnlyChips } from './ReadOnlyChips'`
    - Remove `export type { ChipItem } from './ReadOnlyChips'`
    - _Requirements: 3.2, 3.3_

- [x] 3. Implement auto-grow textarea in ChatInput
  - [x] 3.1 Add auto-grow textarea logic
    - Add `textareaRef` via `useRef<HTMLTextAreaElement>(null)`
    - Add `maxHeightRef` with fallback of `400` (20 * 20px)
    - Add `MAX_ROWS = 20` constant
    - Compute `maxHeight` from `getComputedStyle(el).lineHeight * MAX_ROWS` at mount via `useEffect`
    - Implement `adjustHeight` callback: reset `style.height` to `'auto'`, set to `min(scrollHeight, maxHeight)`, toggle `overflow-y` between `'auto'` and `'hidden'`
    - Call `adjustHeight` on every `inputValue` change via `useEffect`
    - Change textarea from `rows={1}` to `rows={2}`
    - Attach `textareaRef` to the textarea element
    - _Requirements: 7.1, 7.2, 7.3, 7.5_

  - [x] 3.2 Add height reset on send
    - In the send handler, after clearing inputValue, clear `el.style.height` to `''` and set `el.style.overflowY` to `'hidden'`
    - This lets `rows={2}` reassert the native 2-line minimum
    - _Requirements: 7.4_

- [x] 4. Checkpoint - Verify removals and auto-grow
  - Ensure all tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.

- [x] 5. Update test files
  - [x] 5.1 Update ChatInput.test.tsx
    - Remove Plugin_Props (`agentSkills`, `agentMCPs`, `agentPlugins`, `isLoadingSkills`, `isLoadingMCPs`, `isLoadingPlugins`, `allowAllSkills`) from test helper / default props
    - Remove `Skill`, `MCPServer`, `Plugin` type imports if only used for Plugin_Props
    - Verify existing file attachment and context file tests still pass
    - _Requirements: 4.1, 4.2, 4.3_

  - [x] 5.2 Update ChatInput.workspace-removal.test.tsx
    - Remove Plugin_Props from test helper / default props
    - Remove unused type imports related to Plugin_Props
    - _Requirements: 4.1, 4.2_

  - [x] 5.3 Update ChatPage.test.tsx
    - Remove Plugin_Props from ChatInput mock expectations or test assertions
    - Verify `enableSkills` and `enableMCP` flag tests still pass
    - _Requirements: 4.1, 6.3_

  - [x] 5.4 Write property test: Textarea height clamping invariant (Property 1)
    - **Property 1: Textarea height clamping invariant**
    - Generate random `scrollHeight` values (0–2000) using `fc.integer`
    - Mock textarea element's `scrollHeight` and `getComputedStyle` lineHeight
    - Assert `el.style.height === min(mockedScrollHeight, maxHeight) + 'px'`
    - Assert `el.style.overflowY === 'auto'` iff `mockedScrollHeight > maxHeight`, `'hidden'` otherwise
    - **Validates: Requirements 7.1, 7.2, 7.3**

  - [x] 5.5 Write property test: Textarea height reset on send (Property 2)
    - **Property 2: Textarea height reset on send**
    - Generate random `scrollHeight` values (0–2000)
    - Set up mock textarea, call adjustHeight, then simulate send reset
    - Assert `textarea.style.height === ''` (empty string)
    - Assert `textarea.style.overflowY === 'hidden'`
    - **Validates: Requirements 7.4**

- [x] 6. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass (`cd desktop && npm test -- --run`), ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Property 3 (enableSkills/enableMCP flag computation) is NOT tested with a new PBT since ChatPage logic is unchanged
- Backend files and frontend service files (skills.ts, mcp.ts, plugins.ts) are NOT modified
- SwarmAgent.property.test.tsx and AgentsModal.property.test.tsx reference `allowAllSkills` on the Agent model type, NOT ChatInput Plugin_Props — they are unaffected
