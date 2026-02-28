# Implementation Plan: SwarmAgent Initialization Status Display

## Overview

This implementation adds a system status API endpoint to the backend and enhances the BackendStartupOverlay component to display initialization status in a CLI-like format during app startup.

## Tasks

- [x] 1. Create backend system status router
  - [x] 1.1 Create `backend/routers/system.py` with Pydantic models and `/status` endpoint
    - Define `DatabaseStatus`, `AgentStatus`, `ChannelGatewayStatus`, `SystemStatusResponse` models
    - Implement `get_system_status()` endpoint that checks database, agent, and channel gateway
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5_
  
  - [x] 1.2 Register system router in `backend/routers/__init__.py` and `backend/main.py`
    - Export `system_router` from `__init__.py`
    - Include router with prefix `/api/system` in `main.py`
    - _Requirements: 1.1_
  
  - [x] 1.3 Write property test for initialized field consistency
    - **Property 2: Initialized Field Consistency**
    - **Validates: Requirements 1.5, 2.4**

- [x] 2. Checkpoint - Backend API ready
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Create frontend system service
  - [x] 3.1 Create `desktop/src/services/system.ts` with TypeScript interfaces and `getStatus()` function
    - Define `SystemStatus`, `DatabaseStatus`, `AgentStatus`, `ChannelGatewayStatus` interfaces
    - Implement `toCamelCase()` conversion for API response
    - Implement `getStatus()` function with timeout handling
    - _Requirements: 3.1, 3.2, 3.3_
  
  - [x] 3.2 Write property test for snake_case to camelCase transformation
    - **Property 3: Snake Case to Camel Case Transformation**
    - **Validates: Requirements 3.2**

- [x] 4. Add i18n translation keys
  - [x] 4.1 Add initialization status translation keys to `desktop/src/locales/en/translation.json`
    - Add keys for "Connecting to backend", "Database initialized", "SwarmAgent ready"
    - Add keys for "Channel gateway started", "system skills bound", "system MCP servers bound"
    - Add keys for error messages
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

- [x] 5. Enhance BackendStartupOverlay component
  - [x] 5.1 Update `desktop/src/components/common/BackendStartupOverlay.tsx` to fetch and display system status
    - Add state for initialization steps and system status
    - Fetch system status after health check succeeds
    - Display CLI-style status items with checkmarks/errors
    - Use monospace font and tree-style indentation
    - Implement sequential animation for status items appearing
    - Handle timeout and errors gracefully (proceed with startup)
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 5.1, 5.2, 5.3, 5.4, 5.5, 7.1, 7.2, 7.3, 7.4, 7.5_

- [x] 6. Checkpoint - Feature complete
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Write unit tests
  - [x] 7.1 Write backend unit tests for system router
    - Test endpoint returns 200 status code
    - Test response contains all required fields
    - Test error handling scenarios
    - _Requirements: 1.6, 1.7_
  
  - [x] 7.2 Write frontend unit tests for system service
    - Test `getStatus()` makes correct API call
    - Test case conversion
    - Test error propagation
    - _Requirements: 3.1, 3.2, 3.3_

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Backend uses Python/FastAPI with snake_case naming
- Frontend uses TypeScript/React with camelCase naming
- The system status display is informational only - app startup should never be blocked by status fetch failures
- Property tests should use `hypothesis` for Python and `fast-check` for TypeScript
